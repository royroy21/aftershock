from datetime import UTC, datetime, timedelta

import pytest

from ingestion.parsing import MergeStub, ParsedEvent
from ingestion.writer import apply_merges, save_page
from quakes.models import Event

pytestmark = pytest.mark.django_db

BASE_TIME = datetime(2026, 11, 1, tzinfo=UTC)  # fixed, so event times are predictable


def make_event(event_id="ev1", updated=BASE_TIME, **overrides):
    fields = {
        "id": event_id,
        "time": BASE_TIME - timedelta(hours=1),
        "source_updated": updated,
        "status": "automatic",
        "magnitude": 4.2,
        "longitude": -153.07,
        "latitude": 60.04,
        "depth_km": 5.0,
        "place": "Somewhere, Alaska",
    }
    return ParsedEvent(**(fields | overrides))


def test_inserts_new_events():
    result = save_page([make_event("a"), make_event("b")])

    assert (result.written, result.skipped) == (2, 0)
    event = Event.objects.get(id="a")
    assert (event.location.x, event.location.y) == (-153.07, 60.04)
    assert event.magnitude == 4.2


def test_rerunning_a_page_is_a_no_op():
    page = [make_event("a"), make_event("b")]
    save_page(page)
    before = {e.id: e.modified_at for e in Event.objects.all()}

    result = save_page(page)

    assert (result.written, result.skipped) == (0, 2)
    assert {e.id: e.modified_at for e in Event.objects.all()} == before


def test_newer_version_overwrites_every_field_but_keeps_created_at():
    save_page([make_event(magnitude=4.2, place="Old place")])
    original = Event.objects.get()

    result = save_page(
        [
            make_event(
                updated=BASE_TIME + timedelta(minutes=5), magnitude=5.1, status="reviewed", place=""
            )
        ]
    )

    assert result.written == 1
    event = Event.objects.get()
    assert (event.magnitude, event.status, event.place) == (5.1, "reviewed", "")
    assert event.created_at == original.created_at
    assert event.modified_at > original.modified_at


@pytest.mark.parametrize(
    "updated", [BASE_TIME - timedelta(minutes=5), BASE_TIME], ids=["older", "same"]
)
def test_older_or_same_version_is_ignored(updated):
    save_page([make_event(updated=BASE_TIME, magnitude=5.1)])

    result = save_page([make_event(updated=updated, magnitude=1.0)])

    assert (result.written, result.skipped) == (0, 1)
    assert Event.objects.get().magnitude == 5.1


def test_deletion_is_stored_as_a_newer_version():
    save_page([make_event()])

    result = save_page([make_event(updated=BASE_TIME + timedelta(minutes=5), status="deleted")])

    assert (result.written, result.deleted) == (1, 1)
    assert Event.objects.get().status == Event.Status.DELETED
    assert not Event.objects.active().exists()


def test_older_copy_cannot_undo_a_deletion():
    save_page([make_event(updated=BASE_TIME + timedelta(minutes=5), status="deleted")])

    save_page([make_event(updated=BASE_TIME, status="reviewed")])

    assert Event.objects.get().status == Event.Status.DELETED


def stub(event_id="ev1", updated=BASE_TIME + timedelta(minutes=5)):
    return MergeStub(id=event_id, source_updated=updated, merged_with=["other1"])


def test_merge_marks_a_stored_event_as_merged():
    save_page([make_event()])

    changed = apply_merges([stub()])

    assert changed == 1
    event = Event.objects.get()
    assert event.status == Event.Status.MERGED
    assert event.source_updated == BASE_TIME + timedelta(minutes=5)
    assert event.magnitude == 4.2  # the rest of the row is kept for the audit trail
    assert not Event.objects.active().exists()


def test_merge_is_idempotent_and_ignores_unknown_ids():
    save_page([make_event()])
    apply_merges([stub()])

    assert apply_merges([stub(), stub(event_id="never-stored")]) == 0
