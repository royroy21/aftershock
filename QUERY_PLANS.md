# Query plans

Evidence that the events API stays fast as the table grows, and the reason for each index.

## Method

The same requests at three sizes of **real** USGS data. The table was emptied, then grown by
ingesting a week, then a month, then a year (each ingest adds to the last):

| Size | Ingest | Rows | Table / indexes |
|---|---|---|---|
| Week | `make ingest` (default) | 4,486 | 1.4 MB / 1.2 MB |
| Month | `--updated-after 2026-10-15` | 19,124 | 5.8 MB / 5.0 MB |
| Year | `--updated-after 2025-11-15` | 200,435 | 59 MB / 54 MB |

At each size: `VACUUM ANALYZE quakes_event`, so the planner's statistics match the table. Then
each request below was sent through the real API, the SQL that the view and cursor pagination
ran (`... ORDER BY time DESC, id DESC LIMIT 51`) was captured, and that SQL was run under
`EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)`. The API call had already run the query, so the plans
are warm.

Requests, all on `/api/v1/events/`:

- Default list: no parameters
- `?min_magnitude=2.5`, `?min_magnitude=5`, `?min_magnitude=6.5`, `?min_magnitude=9`
- Last 7 days: `?start=2026-11-06T00:00:00Z&end=2026-11-13T00:00:00Z`
- Deep cursor page: a cursor whose position is `2026-01-01 00:00:00+00:00`
- 100 km around Anchorage: `?lat=61.2&lon=-149.9&radius_km=100`
- 100 km around London: `?lat=51.5&lon=-0.13&radius_km=100`

**Buffers** (8 kB pages read) and the plan shape don't depend on the machine, so they're the
numbers to compare. Timings are from a laptop.

## Results

Buffers and execution time at each size. ⚠ marks a request whose plan changed between sizes.

| Request | Plan at a year | Week | Month | Year |
|---|---|---|---|---|
| Default list | Index Scan Backward on `time`, stops after 51 rows | 5 · 0.02 ms | 5 · 0.03 ms | 8 · 0.02 ms |
| Last 7 days (`start`/`end`) | Index range on `time` | 5 · 0.02 ms | 5 · 0.02 ms | 6 · 0.02 ms |
| Deep cursor page (before 2026-01-01) | Index range on `time` | 5 · 0.02 ms | 17 · 0.02 ms | 5 · 0.02 ms |
| `min_magnitude=2.5` | `time` index + filter | 14 · 0.03 ms | 14 · 0.03 ms | 22 · 0.03 ms |
| `min_magnitude=5` | `time` index + filter | 135 · 0.2 ms | 982 · 0.3 ms | 990 · 0.3 ms |
| ⚠ `min_magnitude=6.5` | `time` index + filter, **150,287 rows removed** | 12 · 0.01 ms | 29 · 0.02 ms | **17,447 · 11.7 ms** |
| `min_magnitude=9` | Index Scan on `magnitude` | 3 · 0.01 ms | 3 · 0.01 ms | 7 · 0.01 ms |
| ⚠ 100 km around Anchorage (dense) | Parallel bitmap scan on GiST `location` → top-N sort | 91 · 0.2 ms | 365 · 0.5 ms | **3,066 · 5.7 ms** |
| 100 km around London (sparse) | Index Scan on GiST `location`, nothing found | 2 · 0.01 ms | 1 · 0.01 ms | 25 · 0.06 ms |

The seven requests not marked ⚠ stay between 0.01 and 0.3 ms at every size.

## What the plans show

- **Most requests don't grow with the table.** The list, a time window, a deep cursor page and
  common magnitude filters read 5–22 buffers at all three sizes, a 45× range. Each is a short
  range on the `time` index that stops after 51 rows. There's no `COUNT(*)` and no `OFFSET`,
  so page 4,000 costs the same as page 1.
- **`min_magnitude=5` levels off.** Postgres walks the `time` index newest-first and discards
  rows until it has 51 matches. M5+ quakes are common enough that this stops within the most
  recent weeks, however much history sits behind them: 982 buffers at a month, 990 at a year.
- **`min_magnitude=6.5` is the one that grows, and the planner changes plan.** At a week and a
  month there are few M6.5+ events, so Postgres reads the `magnitude` index directly. At a year
  it switches to walking the `time` index. It assumes magnitude and time are independent, so it
  expects M6.5+ quakes spread evenly through time and an early stop. In this data they cluster
  in older events, so it reads 150k rows to find 51. This cost grows with the amount of history.
- **Radius search scales with the area, not the table.** Anchorage gets denser as data
  accumulates (4,400 candidates in the bounding box at a year), and the cost follows: 91 →
  3,066 buffers. London, which has few quakes, stays at a handful of buffers. At a year Postgres
  adds a parallel worker for Anchorage.
- **The `id` tie-breaker costs almost nothing.** `ORDER BY time DESC, id DESC` uses the `time`
  index plus an *incremental sort*, which only re-sorts rows sharing a timestamp. A `(time, id)`
  index would remove that step but save microseconds, so it wasn't added.

## Index added: `magnitude`

Migration `quakes/migrations/0004_event_magnitude_index.py`. Added after an earlier run at a year
without it: a filter that matches little or nothing (`min_magnitude=9`) did a parallel full table
scan, taking **15 ms and 7,516 buffers**. With the index it's an index lookup, **0.01 ms and 7
buffers**, at every size above.

It doesn't fix `min_magnitude=6.5` at a year (above): adding the index doesn't change that plan.
