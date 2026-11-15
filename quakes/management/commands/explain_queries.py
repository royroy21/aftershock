"""`manage.py explain_queries`: EXPLAIN (ANALYZE, BUFFERS) the SQL behind typical API requests.

Each request goes through the real API (Django's test client), so the plans show exactly what the
view and cursor pagination run. The evidence behind QUERY_PLANS.md; re-run it after loading data.
"""

import base64
from urllib.parse import parse_qs, urlencode, urlparse

from django.core.management.base import BaseCommand
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

LIST_URL = "/api/v1/events/"

REQUESTS = {
    "Default list": "",
    "min_magnitude=2.5 (common)": "?min_magnitude=2.5",
    "min_magnitude=5 (rare)": "?min_magnitude=5",
    "min_magnitude=6.5 (very rare)": "?min_magnitude=6.5",
    "min_magnitude=9 (a handful of matches)": "?min_magnitude=9",
    "Last 7 days": "?start=2026-11-06T00:00:00Z&end=2026-11-13T00:00:00Z",
    "100 km around Anchorage (dense)": "?lat=61.2&lon=-149.9&radius_km=100",
    "100 km around London (sparse)": "?lat=51.5&lon=-0.13&radius_km=100",
}


class Command(BaseCommand):
    help = "Print the query plan for the SQL behind typical API requests."

    def handle(self, *args, **options):
        # Django's test client calls the view in-process (no server needed). It runs against the
        # real database here, not a test one, because this isn't a test run.
        client = Client(HTTP_HOST="localhost")

        # Full URL for each case. The deep page is added here, not in REQUESTS, because building
        # its cursor needs a real one from the API.
        requests = {name: LIST_URL + query for name, query in REQUESTS.items()}
        requests["Deep cursor page (before 2026-01-01)"] = (
            LIST_URL + "?cursor=" + self._cursor_at(client, "2026-01-01 00:00:00+00:00")
        )

        for name, url in requests.items():
            # 1. Call the API, recording every SQL statement it runs.
            with CaptureQueriesContext(connection) as queries:
                response = client.get(url)
            # 2. Keep the event query: the last statement that reads the events table.
            sql = [q["sql"] for q in queries if "quakes_event" in q["sql"]][-1]
            # 3. Run it again under EXPLAIN ANALYZE: the plan Postgres chose, with real row counts,
            #    timings and pages read (BUFFERS). The API call above has warmed the cache.
            with connection.cursor() as cursor:
                cursor.execute("EXPLAIN (ANALYZE, BUFFERS, COSTS OFF) " + sql)
                plan = "\n".join(row[0] for row in cursor.fetchall())
            # 4. Print the case, how many events it returned, its URL and the plan.
            results = len(response.json()["results"])
            self.stdout.write(f"\n### {name} ({results} results)\n{url}\n\n{plan}\n")

    def _cursor_at(self, client, position):
        """A cursor for the page starting at `position`, encoded the way DRF encodes its own."""
        # A DRF cursor is base64 of a small query string, e.g. "p=2026-11-15+04:16:49+00:00",
        # where `p` is the `time` of the last event already seen. Take a real cursor from page 1,
        # swap in our own `p` and re-encode it, to jump to a deep page without paging through.
        next_url = client.get(LIST_URL).json()["next"]
        token = parse_qs(urlparse(next_url).query)["cursor"][0]
        params = parse_qs(base64.b64decode(token).decode()) | {"p": [position]}
        return base64.b64encode(urlencode(params, doseq=True).encode()).decode()
