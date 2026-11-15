from datetime import UTC, datetime

import pytest
import responses

from ingestion.client import QUERY_URL, USGSClient, USGSError, build_session

UPDATED_AFTER = datetime(2026, 11, 1, tzinfo=UTC)


def make_client():
    # No backoff so retry tests don't sleep.
    return USGSClient(session=build_session(retries=3, backoff_factor=0), page_size=2)


def page(n):
    return {"type": "FeatureCollection", "features": [{"id": f"ev{i}"} for i in range(n)]}


@responses.activate
def test_retries_transient_errors_then_succeeds():
    responses.get(QUERY_URL, status=503)
    responses.get(QUERY_URL, status=429)
    responses.get(QUERY_URL, json=page(1))

    pages = list(make_client().iter_pages(UPDATED_AFTER))

    assert pages == [[{"id": "ev0"}]]


@pytest.mark.parametrize("status", [503, 400])
@responses.activate
def test_failures_raise_usgs_error(status):
    responses.get(QUERY_URL, status=status)

    with pytest.raises(USGSError):
        list(make_client().iter_pages(UPDATED_AFTER))


@responses.activate
def test_no_content_is_an_empty_page():
    responses.get(QUERY_URL, status=204)

    assert list(make_client().iter_pages(UPDATED_AFTER)) == [[]]


@pytest.mark.parametrize(
    "body", ["<html>maintenance</html>", '{"type": "FeatureCollection"}', '{"features": 5}']
)
@responses.activate
def test_unusable_body_raises(body):
    responses.get(QUERY_URL, body=body, content_type="application/json")

    with pytest.raises(USGSError):
        list(make_client().iter_pages(UPDATED_AFTER))
