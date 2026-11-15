from datetime import UTC, datetime, timedelta

import pytest
from django.contrib.gis.geos import Point
from django.urls import reverse

from quakes.models import Event

pytestmark = pytest.mark.django_db

BASE_TIME = datetime(2026, 11, 1, tzinfo=UTC)  # fixed, so event times are predictable
LIST_URL = reverse("event-list")
LONDON = {"lat": 51.5072, "lon": -0.1276}


def make_event(
    event_id, hours_ago=0, magnitude=3.0, status=Event.Status.REVIEWED, lon=-153.07, lat=60.04
):
    return Event.objects.create(
        id=event_id,
        time=BASE_TIME - timedelta(hours=hours_ago),
        source_updated=BASE_TIME,
        status=status,
        magnitude=magnitude,
        location=Point(lon, lat, srid=4326),
        depth_km=5.0,
    )


def ids(response):
    return [event["id"] for event in response.json()["results"]]


def test_lists_newest_first_with_flat_coordinates(client):
    make_event("older", hours_ago=2)
    make_event("newer", hours_ago=1)

    response = client.get(LIST_URL)

    assert ids(response) == ["newer", "older"]
    first = response.json()["results"][0]
    assert (first["latitude"], first["longitude"]) == (60.04, -153.07)
    assert first["distance_km"] is None  # only set by a radius search


def test_hides_deleted_and_merged_events(client):
    make_event("live")
    make_event("gone", status=Event.Status.DELETED)
    make_event("dupe", status=Event.Status.MERGED)

    assert ids(client.get(LIST_URL)) == ["live"]
    assert client.get(reverse("event-detail", args=["gone"])).status_code == 404
    assert client.get(reverse("event-detail", args=["dupe"])).status_code == 404


@pytest.mark.parametrize("params", [{}, LONDON | {"radius_km": 50}], ids=["list", "radius"])
def test_list_is_one_query(client, django_assert_num_queries, params):
    for i in range(5):
        make_event(f"ev{i}", hours_ago=i, lon=LONDON["lon"], lat=LONDON["lat"])
    # Warm-up: GeoDjango looks up the SRID definition once per process, then caches it.
    client.get(LIST_URL, params)

    with django_assert_num_queries(1):  # one SELECT: no N+1, and cursor paging needs no COUNT(*)
        client.get(LIST_URL, params)


def test_radius_search_returns_nearby_events_with_distance(client):
    make_event("london", lon=-0.1276, lat=51.5072)
    make_event("oxford", lon=-1.2577, lat=51.7520)  # ~82 km from London
    make_event("paris", lon=2.3522, lat=48.8566)  # ~344 km from London

    response = client.get(LIST_URL, LONDON | {"radius_km": 100})

    results = {event["id"]: event["distance_km"] for event in response.json()["results"]}
    assert set(results) == {"london", "oxford"}
    assert results["london"] == 0


def test_errors_share_one_envelope(client):
    invalid = client.get(LIST_URL, {"min_magnitude": "big"})
    missing = client.get(reverse("event-detail", args=["nope"]))

    assert invalid.status_code == 400
    assert invalid.json() == {
        "error": {
            "code": "invalid",
            "message": "Invalid request.",
            "details": {"min_magnitude": ["A valid number is required."]},
        }
    }
    assert missing.status_code == 404
    assert missing.json() == {
        "error": {
            "code": "not_found",
            "message": "No Event matches the given query.",
            "details": None,
        }
    }
