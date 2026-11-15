"""Writes parsed USGS events to the database using the "strictly newer `updated` wins" rule."""

from dataclasses import dataclass

from django.contrib.gis.geos import Point
from django.utils import timezone

from ingestion.parsing import MergeStub, ParsedEvent
from quakes.models import Event

# Every column a newer version replaces. `created_at` is kept from the first insert;
# `modified_at` is refreshed by auto_now, which bulk_create applies.
UPDATE_FIELDS = [f.name for f in Event._meta.concrete_fields if f.name not in ("id", "created_at")]


@dataclass(frozen=True)
class WriteResult:
    written: int  # new events, or newer versions of stored ones
    skipped: int  # not newer than what we hold (includes in-page duplicates)
    deleted: int  # of `written`, how many were USGS deletions


def save_page(events: list[ParsedEvent]) -> WriteResult:
    """Upsert one page of events, ignoring anything not strictly newer than what we store.

    Re-running a page is a no-op, so retried or overlapping fetches are safe. Call inside a
    transaction (ingest() runs each run in one); the read-then-write relies on it and on there
    being a single writer, which the ingest service's lock guarantees.
    """
    latest = _newest_per_id(events)

    stored = dict(Event.objects.filter(id__in=latest.keys()).values_list("id", "source_updated"))
    to_write = [
        event
        for event in latest.values()
        if event.id not in stored or event.source_updated > stored[event.id]
    ]
    if to_write:
        Event.objects.bulk_create(
            [_to_model(event) for event in to_write],
            update_conflicts=True,
            unique_fields=["id"],
            update_fields=UPDATE_FIELDS,
        )

    return WriteResult(
        written=len(to_write),
        skipped=len(events) - len(to_write),
        deleted=sum(event.status == Event.Status.DELETED for event in to_write),
    )


def apply_merges(stubs: list[MergeStub]) -> int:
    """Mark rows whose id USGS has merged into another event. Returns how many rows changed.

    Uses the same rule as save_page: only a strictly newer `updated` changes a row, so re-runs
    are no-ops and a later full record for the id (if USGS un-merges it) wins again. A stub for
    an id we never stored needs nothing; the surviving event arrives under its own id.
    """
    changed = 0
    for stub in stubs:
        changed += Event.objects.filter(id=stub.id, source_updated__lt=stub.source_updated).update(
            status=Event.Status.MERGED,
            source_updated=stub.source_updated,
            modified_at=timezone.now(),
        )
    return changed


def _newest_per_id(events: list[ParsedEvent]) -> dict[str, ParsedEvent]:
    # Postgres rejects an upsert that touches the same row twice, so collapse duplicates first.
    latest: dict[str, ParsedEvent] = {}
    for event in events:
        current = latest.get(event.id)
        if current is None or event.source_updated > current.source_updated:
            latest[event.id] = event
    return latest


def _to_model(event: ParsedEvent) -> Event:
    fields = event.model_dump(exclude={"longitude", "latitude"})
    return Event(**fields, location=Point(event.longitude, event.latitude, srid=4326))
