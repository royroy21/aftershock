import copy
from datetime import UTC, datetime

import pytest

from ingestion.parsing import MergeStub, parse_page, parse_raw_event

# Real raw events captured from the USGS API (trimmed to the fields we read).
AUTOMATIC = {
    "type": "Feature",
    "id": "aka2026wlayjd",
    "properties": {
        "mag": 0.7,
        "place": "64 km ENE of Pedro Bay, Alaska",
        "time": 1794499266170,
        "updated": 1794499308776,
        "url": "https://earthquake.usgs.gov/earthquakes/eventpage/aka2026wlayjd",
        "felt": None,
        "alert": None,
        "status": "automatic",
        "tsunami": 0,
        "sig": 8,
        "net": "ak",
        "magType": "ml",
        "type": "earthquake",
        "title": "M 0.7 - 64 km ENE of Pedro Bay, Alaska",
    },
    "geometry": {"type": "Point", "coordinates": [-153.073, 60.044, 5]},
}

DELETED_WITHOUT_ID = {
    "type": "Feature",
    "id": "",
    "properties": {
        "mag": 0.8,
        "place": None,
        "time": 1794396345901,
        "updated": 1794429285895,
        "status": "deleted",
        "net": None,
        "magType": None,
        "type": "earthquake",
    },
    "geometry": {"type": "Point", "coordinates": [-104.368, 31.66, 5.9717]},
}

# Real stub: the Alaska network's id after USGS merged it into the volcano observatory's event.
MERGE_STUB = {
    "type": "Feature",
    "id": "aka2026vsvfmy",
    "properties": {
        "mag": None,
        "place": "83 km NW of Karluk, Alaska",
        "time": None,
        "updated": 1794418319706,
        "status": None,
        "net": "ak",
        "ids": ",av94482093,aka2026vsvfmy,",
        "magType": None,
        "type": "earthquake",
        "title": "M ? - 83 km NW of Karluk, Alaska",
    },
    "geometry": {"type": "Point", "coordinates": [0, 0, None]},
}


def with_changes(raw_event=AUTOMATIC, **props):
    changed = copy.deepcopy(raw_event)
    changed["properties"].update(props)
    return changed


def test_parses_a_real_event():
    event = parse_raw_event(AUTOMATIC)

    assert event.id == "aka2026wlayjd"
    assert event.time == datetime(2026, 11, 12, 16, 1, 6, 170000, tzinfo=UTC)
    assert event.source_updated > event.time
    assert (event.longitude, event.latitude, event.depth_km) == (-153.073, 60.044, 5)
    assert event.magnitude == 0.7
    assert event.status == "automatic"
    assert event.tsunami is False
    assert event.alert == ""  # null text becomes empty string
    assert event.felt is None  # null numbers stay null


@pytest.mark.parametrize(
    "props",
    [
        {"status": "mystery"},
        {"time": None},
        {"updated": "yesterday"},
        {"mag": float("nan")},
    ],
)
def test_rejects_untrustworthy_values(props):
    page = parse_page([with_changes(**props)])

    assert len(page.rejected) == 1


@pytest.mark.parametrize("coordinates", [[200, 0], [0, -91], [0]])
def test_rejects_impossible_coordinates(coordinates):
    raw_event = copy.deepcopy(AUTOMATIC)
    raw_event["geometry"]["coordinates"] = coordinates

    page = parse_page([raw_event])

    assert len(page.rejected) == 1


def test_recognises_a_merge_stub():
    page = parse_page([MERGE_STUB, AUTOMATIC])

    assert page.rejected == []
    assert [e.id for e in page.events] == ["aka2026wlayjd"]
    (stub,) = page.merges
    assert isinstance(stub, MergeStub)
    assert (stub.id, stub.merged_with) == ("aka2026vsvfmy", ["av94482093"])
    assert stub.source_updated == datetime(2026, 11, 11, 17, 31, 59, 706000, tzinfo=UTC)


def test_record_without_time_or_status_and_no_other_ids_is_rejected_not_a_merge():
    # Only an `ids` list naming another event makes it a merge; otherwise it's just broken.
    lone = copy.deepcopy(MERGE_STUB)
    lone["properties"]["ids"] = ",aka2026vsvfmy,"

    page = parse_page([lone])

    assert page.merges == []
    assert len(page.rejected) == 1
