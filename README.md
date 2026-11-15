# Aftershock

A small service that keeps a local copy of the USGS earthquake catalogue up to date and serves it
through a fast, filterable JSON API: by time, magnitude and distance from a point.

Django 5.2 · DRF · PostgreSQL 17 + PostGIS · Celery + Redis · Docker 29.1.3 · Docker Compose 5.0.1

This project makes use of a Makefile found at the root. This provides shortcuts to command commands used.
To view all commands available run `make` at root.

## Quick start

```bash
make up                  # db, redis, migrations, web on :8000, worker. No .env or keys needed.
make ingest              # in another terminal: the initial ingest, the last 7 days (~1 min)
```

The API is empty until the initial ingest. For a longer history:
`make ingest ARGS="--updated-after 2025-11-15"` (a year is ~200k events and ~50 minutes).

**Daily ingest:** once the initial ingest has run without errors, turn on the scheduler, which
ingests everything USGS has updated since, once a day (03:00 UTC):

```bash
echo "COMPOSE_PROFILES=scheduler" >> .env
make up        # now also starts `beat`
```

Example requests:

```bash
# Newest first, 50 per page (?page_size= up to 500)
curl "localhost:8000/api/v1/events/"

# Magnitude 4.5 and above since 1 November
curl "localhost:8000/api/v1/events/?min_magnitude=4.5&start=2026-11-01T00:00:00Z"

# Within 100 km of Anchorage, with the distance of each event
curl "localhost:8000/api/v1/events/?lat=61.2&lon=-149.9&radius_km=100"

# Next page: follow the "next" URL from any list response (cursor pagination)
curl "localhost:8000/api/v1/events/?cursor=<value from next>"

# One event (copy an id from the list)
curl "localhost:8000/api/v1/events/<id>/"

# Errors share one shape
curl "localhost:8000/api/v1/events/?min_magnitude=big"
# {"error": {"code": "invalid", "message": "Invalid request.",
#            "details": {"min_magnitude": ["A valid number is required."]}}}
```

| | |
|---|---|
| API docs (OpenAPI / Swagger) | http://localhost:8000/api/docs/ |
| Admin (read-only) | http://localhost:8000/admin/ after `make manage ARGS=createsuperuser` |
| Tests | `make test` |
| Lint | `make lint` |
| Query plans | `make explain`, written up in [QUERY_PLANS.md](QUERY_PLANS.md) |

Filters on `/api/v1/events/`: `start`, `end` (origin time, ISO 8601), `min_magnitude`,
`max_magnitude`, and `lat` + `lon` + `radius_km` (all three together, radius up to 2,000 km).

## Track and data source

**Track 2, ingest and serve at scale**, using the **USGS FDSN event API**. It's keyless,
well documented and live: about 400 new events a day, plus constant revisions. That makes
re-runs and messy records a real problem rather than a hypothetical one: events are revised for
months, deleted, and merged when two seismic networks report the same quake. Locations make a
spatial query a natural feature, so the API has a PostGIS radius search.

## Architecture and key decisions

```
beat (daily) ───────────────────────┐
you ──> manage.py ingest ──> Celery task (Redis) ──> ingest()
               └── --sync ─────────────────────────────┘
ingest(): USGS client ──> parsing (validate) ──> writer (upsert) ──> PostgreSQL/PostGIS <── DRF API
```

**Services** (`docker-compose.yml`): `db` (PostGIS), `redis`, a one-off `migrate` service that
must succeed before `web` and `worker` start (so neither runs against an old schema, and replicas
never race to migrate), `web` (`runserver`, dev only), `worker` (Celery) and `beat` (Celery beat, only with
`COMPOSE_PROFILES=scheduler`).

**Ingest** (`ingestion/`), Celery-independent; the task and the management command are thin
wrappers:

- `client.py`: offset paging through `/query`, ordered by origin time; retries 429/5xx and
  dropped connections with exponential backoff (about 6 minutes in all).
- `parsing.py`: pydantic validation. Bad records are rejected with a reason and counted; they
  never fail the page.
- `writer.py`: one `INSERT … ON CONFLICT (id) DO UPDATE` per page, for records that are new or
  whose USGS `updated` is **strictly newer** than ours. Re-runs and overlapping pages are no-ops.
- `ingest.py`: **each run is one transaction**, guarded by a Postgres advisory lock so only one
  run can happen at a time (a second one exits immediately). The next run starts at
  `MAX(source_updated) − 10 minutes`.

  *Why one transaction:* USGS can only order results by origin time, not by `updated`. If a crashed
  run had kept its first pages, the table could hold a later `updated` than records on pages it
  never fetched, and starting from the table's maximum would skip those records forever. With the
  whole run in one transaction, a crash leaves nothing behind. The cost: a long backfill is
  all-or-nothing (see limitations). The thinking is this - when the app is first started we manually
  ingest for a selected time period (a year?) then we set celery beat to be active (see .env.example)
  which runs ingest once a day - meaning the one transaction for ingest isn't a problem as one day ingest
  is quite fast.
- **Scheduling:** Celery beat enqueues `ingest_task` once a day; the worker runs it. It's off by
  default so the first, larger ingest is run and checked by hand. If a run overlaps the next,
  the lock makes the second one exit. Each run picks up everything updated since the last, so
  the interval only sets how fresh the data is.

**Data model** (`quakes.Event`): the USGS id as primary key, origin `time`, `source_updated`,
`status`, magnitude, a PostGIS `geography` point, depth and descriptive fields. Deletions and
merges are stored as a status (`deleted`, `merged`) rather than removing rows: one write path, an
audit trail, and an older copy can never resurrect a deleted event. User-facing code uses
`Event.objects.active()`, which hides both.

**API** (`quakes/`): read-only `ReadOnlyModelViewSet`. Filters are validated by a small DRF
serializer and applied in `get_queryset()`. **Cursor pagination** on `(time, id)`: no `COUNT(*)`,
the same cost at any depth, and stable while new events arrive. Radius search uses `ST_DWithin` on
the geography column: correct in km, across the antimeridian, and served by the GiST index. One
error shape for every API error (`aftershock/exceptions.py`).

**Indexes**, from query plans on real data at three sizes, a week to a year (4.5k to 200k rows,
[QUERY_PLANS.md](QUERY_PLANS.md)): `time` (list and paging), GiST `location` (radius),
`source_updated` (ingest start point) and `magnitude` (stops rare magnitude filters becoming full
scans). Most requests read the same handful of pages at every size.

### USGS quirks handled

- **Merge stubs.** When two networks' reports are merged, the absorbed id comes back with no time
  or status and an `ids` list naming the survivor. We mark that row `merged`.
- **Deletions without an id** (about 1% of a year's records): nothing identifies which event they
  delete, so they're rejected and counted (see limitations).
- Epoch-millisecond timestamps, `[lon, lat, depth]` coordinates, `NaN`/`Infinity` rejected (Postgres
  sorts NaN above every number, so it would match every `min_magnitude` filter).
- **503s and dropped connections on long runs:** two one-year backfills failed after 30 seconds
  of retries; patient backoff (8 retries, up to ~6 min) got the third through. This has been tested
  since without 503 errors.

## Tests and QA

`make test` (pytest, against the real PostGIS database). The tests sit where our own logic is,
and leave out what Django, DRF, pydantic and urllib3 already test:

- `ingestion/tests/test_parsing.py`: real USGS records (captured fixtures), merge stubs, every
  rejection rule.
- `ingestion/tests/test_writer.py`: the "strictly newer wins" rule: inserts, re-runs are no-ops,
  older copies can't undo a deletion or merge, duplicate ids within a page.
- `ingestion/tests/test_client.py`: retries then succeeds, failures become `USGSError`, empty and
  unusable responses.
- `quakes/tests/test_api.py`: ordering and shape, deleted/merged hidden, **one SQL query per
  request** (list and radius), radius distance, the error shape.

The ingest orchestration (`ingest.py`: lock, transaction, start point) is checked by the manual
test plan below rather than unit tests, since it's thin glue over the tested parts plus Postgres.

### Manual test plan

1. **Fresh start:** `docker compose down -v && docker compose up`. `migrate` exits 0, then `web` and
   `worker` start. `curl localhost:8000/api/v1/events/` returns an empty list.
2. **First ingest:** `make ingest`. The log shows `Ingest started (updated_after=<7 days ago>)` and
   `Ingest succeeded: fetched=… written=…`; the list now has events.
3. **Re-run is a no-op:** `make ingest` again. It starts 10 minutes before the newest `updated`,
   and nearly everything is `skipped`.
4. **One run at a time:** start `make ingest ARGS="--updated-after 2026-10-01"` and, while it runs,
   `make ingest` in a second terminal. The second exits with *"Another ingest is already running"*.
5. **A crash writes nothing:** start a long backfill (`--updated-after 2026-09-01`), note the row
   count, kill it with Ctrl+C midway. The count is unchanged, and the next `make ingest` starts
   from the same point as before.
6. **Queued path:** `docker compose run --rm web python manage.py ingest` (no `--sync`) enqueues
   a task; `docker compose logs worker` shows the run.
7. **API checks:** the example requests above; a partial radius search (`?lat=61.2`) returns a 400
   naming the missing parameters; `?radius_km=5000` is rejected; a deleted event's id gives 404.
8. **Merge:** in `make shell`, create an `Event` with the id of a known merge stub (e.g.
   `aka2026vsvfmy`) and an old `source_updated`, then `make ingest ARGS="--updated-after
   2026-11-11T17:00"`. The run reports `merged=1` and the row's status is `merged`.
9. **Scheduler:** plain `docker compose up` starts no `beat`. With `COMPOSE_PROFILES=scheduler`,
   `docker compose logs beat` shows it started. To see a scheduled run without waiting a day, set
   the crontab in `CELERY_BEAT_SCHEDULE` to `crontab()` (every minute), restart `beat` and
   `worker`, and the worker logs `Ingest succeeded`. Then revert.

## Known limitations

- **Long backfills are all-or-nothing.** A one-year backfill takes ~50 minutes, and a USGS outage
  longer than the retry window (~6 min) loses the whole run. The fix is resumable chunks with
  their own bookkeeping, which this version deliberately doesn't have as this would mean a table
  to hold where the chunk should resume which adds quite a bit of complexity. The all-or-nothing
  approach here only really is a problem when running initial ingest which takes a while to complete.
- **Deletions that USGS sends without an id** can't be matched. If we stored that event earlier,
  our copy stays visible.
- **No ingest run history:** each run logs one summary line (`fetched=… written=… rejected=…`),
  but nothing is stored.
- **Unknown URLs and server errors** return Django's HTML pages, not the JSON error shape.
- **No authentication or rate limiting.** The data is public and the API read-only.
- **`runserver` and DEBUG** are development settings.

## Before production

- **Serving:** gunicorn (or uvicorn) behind a reverse proxy, `DEBUG=False`, static files via
  WhiteNoise or a CDN, secrets from the platform's secret store, and `ALLOWED_HOSTS` set properly.
  JSON `handler404`/`handler500` so every response matches the error shape.
- **Health and readiness:** a `/healthz` endpoint that checks the database, for the load balancer
  and orchestrator.
- **Scheduling and visibility:** run beat as a single replica (or swap it for a platform
  CronJob), more often than daily if fresher data is needed; persist each run's stats (or export
  them as metrics) and alert on failures, a growing `rejected` count, or no successful run in N
  hours.
- **Resumable backfills:** split large backfills into origin-time windows with a small progress
  table, so an outage costs one window, not the whole run, while keeping the start point safe.
- **API protection:** DRF throttling per IP or API key, and a cache (or CDN) in front of the list
  endpoint, since most traffic is the same first page.

## How I used AI, and where I overruled it

I built this with Claude Code, in small reviewed steps: design discussion first, then one file at
a time, with tests and lint run in Docker after each change. AI did most of the typing,
investigated the USGS API, ran the end-to-end checks and query plans, and explained trade-offs on
request. I reviewed every file and made the calls. Where I overruled or changed its suggestions:

- **httpx → requests:** no async need, and urllib3's `Retry` already does backoff.
- **No time-window bisection for paging:** the API paginates, and idempotent writes absorb any drift.
- **Celery beat off by default, daily:** AI proposed beat every few minutes from startup. I made
  it opt-in after a manual first ingest, once a day.
- **No startup backfill.** Ingest starts from the data already stored, defaulting to the last 7
  days so a fresh checkout is usable in a minute.
- **Ingest independent of Celery:** the task and the management command are thin wrappers.
- **`runserver` instead of gunicorn/WhiteNoise** for a dev setup, with production noted above.
- **Migrations moved out of the web command** into their own one-off service.
- **Health checks trimmed** to the one something actually waits on (the database).
- **An `IngestRun` table removed:** AI proposed it to protect the start point after a crash; I
  replaced it with one transaction per run, which is simpler but makes long backfills
  all-or-nothing (a trade-off I accepted, documented above).
- **django-filter removed:** filtering moved into `get_queryset()` with a small serializer, which
  is more explicit, fixed an inconsistent error key.
- **Status kept strict** after checking the USGS docs, reusing `Event.Status` rather than a
  duplicate list.
- **Tests trimmed** to our own logic, dropping ones that re-tested DRF, pydantic or urllib3.
- Many smaller simplifications: naming (`writer.py`, `raw_event`, `BASE_TIME`), removing
  defensive code that guarded against nothing, and a single consistent error shape.

What AI found that I wouldn't have spotted as quickly: the 30-day `starttime` default, merge
stubs, id-less deletions, Postgres's NaN ordering, the migrate container running a stale image,
and that USGS needs a much more patient retry policy on long runs.
