"""`manage.py ingest`: enqueue an ingest on Celery (default), or run it here with --sync.

Designed to be called by an external scheduler such as cron. Overlapping runs are safe:
only one can hold the ingest lock, and the others exit without doing anything.
"""

import argparse
from datetime import UTC, datetime

from django.core.management.base import BaseCommand, CommandError

from ingestion.client import USGSError
from ingestion.ingest import IngestAlreadyRunning, ingest
from ingestion.tasks import ingest_task


def aware_datetime(value: str) -> datetime:
    """Parse an ISO 8601 date or datetime; naive values are taken as UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 date/datetime: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class Command(BaseCommand):
    help = "Ingest events updated in USGS since the newest update already stored."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Run in this process instead of enqueueing a Celery task.",
        )
        parser.add_argument(
            "--updated-after",
            type=aware_datetime,
            help="Override the start point, e.g. 2026-01-01 (default: newest stored update).",
        )

    def handle(self, *args, sync, updated_after, **options):
        if not sync:
            iso = updated_after.isoformat() if updated_after else None
            result = ingest_task.delay(iso)
            self.stdout.write(f"Enqueued ingest task {result.id}")
            return

        try:
            stats = ingest(updated_after=updated_after)
        except IngestAlreadyRunning as exc:
            raise CommandError("Another ingest is already running; nothing to do.") from exc
        except USGSError as exc:
            raise CommandError(f"Ingest failed, USGS unavailable: {exc}") from exc

        self.stdout.write(self.style.SUCCESS(f"Ingest succeeded: {stats}"))
