"""HTTP client for the USGS FDSN event API. Knows about HTTP and paging, not our models."""

import logging
from collections.abc import Iterator
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"

# The API defaults `starttime` to 30 days ago even when `updatedafter` is given, which silently
# drops revisions to older events. Pinning it far back makes `updatedafter` the only filter.
EARLIEST_ORIGIN_TIME = "1900-01-01"


class USGSError(Exception):
    """USGS could not be reached or returned something we can't use."""


def build_session(retries: int = 8, backoff_factor: float = 2.0) -> requests.Session:
    """Session that retries transient failures with exponential backoff.

    Waits roughly 4, 8, 16, 32, 64, 120, 120 s between attempts (~6 min in all): USGS returns
    503s under load for longer than a few seconds, and in a long run one give-up loses it all.

    Retries connection errors, read timeouts, 429 and 5xx (honouring Retry-After). Never retries
    other 4xx: a 400 means our request is wrong and repeating it won't help.
    """
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "aftershock-ingest/1.0"
    return session


class USGSClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        page_size: int = 1000,
        timeout: tuple[float, float] = (5, 60),
    ):
        # page_size <= 1000 with time ordering keeps us clear of the API's planned
        # time-range limits for large worldwide queries.
        self.session = session or build_session()
        self.page_size = page_size
        self.timeout = timeout  # (connect, read) seconds

    def iter_pages(self, updated_after: datetime) -> Iterator[list[dict]]:
        """Yield pages of raw events (GeoJSON features) updated after `updated_after`, oldest
        origin first.

        Uses offset paging. If USGS changes data mid-run, an event can shift between pages and
        appear twice; that's harmless because writes are idempotent.
        """
        offset = 1  # the API's offset is 1-based
        while True:
            raw_events = self._fetch_page(updated_after, offset)
            yield raw_events
            if len(raw_events) < self.page_size:
                return
            offset += self.page_size

    def _fetch_page(self, updated_after: datetime, offset: int) -> list[dict]:
        params = {
            "format": "geojson",
            "updatedafter": updated_after.isoformat(),
            "starttime": EARLIEST_ORIGIN_TIME,
            "orderby": "time-asc",
            "includedeleted": "true",
            "limit": self.page_size,
            "offset": offset,
        }
        try:
            response = self.session.get(QUERY_URL, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise USGSError(f"USGS request failed (offset={offset}): {exc}") from exc

        if response.status_code == 204:  # the API's "no data" response
            return []

        try:
            raw_events = response.json()["features"]
        except (ValueError, KeyError, TypeError) as exc:
            raise USGSError(f"Unexpected USGS response body (offset={offset})") from exc
        if not isinstance(raw_events, list):
            raise USGSError(f"USGS 'features' is not a list (offset={offset})")

        logger.info("Fetched %d raw events from USGS (offset=%d)", len(raw_events), offset)
        return raw_events
