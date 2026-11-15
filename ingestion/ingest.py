"""The ingest service: fetch → parse → write, one run at a time. Has no Celery dependency."""

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from ingestion.client import USGSClient
from ingestion.parsing import parse_page
from ingestion.writer import apply_merges, save_page
from quakes.models import Event

logger = logging.getLogger(__name__)

# How far back the very first run reaches; use --updated-after for a longer backfill.
DEFAULT_LOOKBACK = timedelta(days=7)
# Re-read a little before the newest `updated` we hold, in case USGS made an event visible
# late (its `updated` earlier than events we already saw). Re-reads are no-ops.
OVERLAP = timedelta(minutes=10)
# Arbitrary app-wide key for the Postgres advisory lock ("USGS" in ASCII).
LOCK_KEY = 0x55534753


class IngestAlreadyRunning(Exception):
    """Another ingest holds the lock; this one did nothing."""


@dataclass
class IngestStats:
    """What one run did. fetched == written + merged + skipped + rejected; `deleted` is the
    subset of `written` that USGS reported as deleted."""

    fetched: int = 0
    written: int = 0
    merged: int = 0
    skipped: int = 0
    deleted: int = 0
    rejected: int = 0

    def __str__(self) -> str:
        return " ".join(f"{name}={value}" for name, value in asdict(self).items())


def ingest(updated_after: datetime | None = None, client: USGSClient | None = None) -> IngestStats:
    """Run one ingest and return what it did.

    `updated_after` overrides the start point (e.g. for a manual backfill); by default the run
    continues from the newest `updated` already stored (see `next_start_point`). The whole run
    is one transaction: a failure writes nothing. Raises IngestAlreadyRunning if another run
    holds the lock.
    """
    client = client or USGSClient()
    stats = IngestStats()

    with transaction.atomic():
        # atomic() makes the run all-or-nothing but doesn't stop a second run; this lock does.
        # Postgres releases it on commit, rollback or a dead process.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_xact_lock(%s)", [LOCK_KEY])
            if not cursor.fetchone()[0]:
                raise IngestAlreadyRunning("another ingest is in progress")

        start = updated_after or next_start_point()
        logger.info("Ingest started (updated_after=%s)", start)
        for raw_events in client.iter_pages(start):
            _process_page(stats, raw_events)

    logger.info("Ingest succeeded: %s", stats)
    return stats


def next_start_point() -> datetime:
    """The newest `updated` we hold, minus OVERLAP; a week ago if we hold nothing.

    Only safe because a run is all-or-nothing. USGS orders results by origin time, not by
    `updated`, so if a crashed run had kept its first pages, the table could hold a later
    `updated` than events on pages never fetched, and starting from it would skip those events
    forever. With the run in one transaction, a crash leaves the table (and so this start
    point) as it was, and the retry fetches everything again.
    """
    newest = Event.objects.aggregate(newest=Max("source_updated"))["newest"]
    if newest is None:
        return timezone.now() - DEFAULT_LOOKBACK
    return newest - OVERLAP


def _process_page(stats: IngestStats, raw_events: list) -> None:
    page = parse_page(raw_events)
    for rejection in page.rejected:
        logger.warning("Rejected USGS event %r: %s", rejection.event_id, rejection.reason)

    result = save_page(page.events)
    merged = apply_merges(page.merges)

    stats.fetched += len(raw_events)
    stats.written += result.written
    stats.merged += merged
    stats.skipped += result.skipped + len(page.merges) - merged
    stats.deleted += result.deleted
    stats.rejected += len(page.rejected)
