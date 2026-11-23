# Measurements

Measured numbers with the date and method. "Pending" means the figure cannot be measured yet; it
is filled in when it can. Nothing here is an estimate unless it says so.

## Transform runtime

| Measure | Before | After | Date | Method |
| --- | --- | --- | --- | --- |
| Transform Lambda, end to end | timed out at the 600 s limit | 54 s | 2026-11-23 | One manual invocation of `headway-transform` in Lambda (900 s timeout, `step_ms` in the summary log line), after curated manifests, table-materialized staging and the run lock. Gold exported and MotherDuck published. |
| Transform, local benchmark | 348 s | 7.6 s | 2026-11-23 | `scripts/bench_transform.py --days 1 --backfill`: one day of replicated ingest manifests behind a local S3-compatible server with per-request latency. Before: one raw JSON object read per manifest. After: curated Parquet. |
| S3 requests in that benchmark | 116,845 | 143 | 2026-11-23 | Same run, counted by the S3 look-alike server. |
| Static GTFS load (`headway-static`) | over 300 s | 2.5 s | 2026-11-23 | Invocation of the static Lambda. Before: whole-feed row-wise parse. After: streamed with pyarrow and filtered to subway scope first. |

Per step of the 54 s Lambda run (2026-11-23, `step_ms`):

| Step | Seconds |
| --- | --- |
| `s3_load` | 23 |
| `dbt_build` | 15 |
| `dbt_test` | 10 |
| `export_gold` | 2 |
| `publish` (MotherDuck) | 3 |
| `cost` | 0.5 (failed that run, see INCIDENTS.md) |

## Volume

Measured 2026-11-23 from the curated Parquet in the raw bucket (`dt=2026-11-23`, the 18 complete
UTC hours 00-17, scaled to 24 h) and the raw manifests.

| Measure | Value | Method |
| --- | --- | --- |
| Captures per day | 4,320 (1,440 runs x 3 feeds) | One run per minute, three feeds. |
| `trip_updates` rows per day | about 2.0 million | 1,499,330 rows in 18 hours x 24/18. Subway scope, after curation. |
| `vehicle_positions` rows per day | about 98,000 | 73,386 rows in 18 hours x 24/18. |
| `alerts` rows per day | about 825,000 | 618,404 rows in 18 hours x 24/18. |
| Gold rows exported | 21,892 across 8 tables (`route_shapes` 18,252; `agg_pipeline_health` 1,562) | `gold/manifest.json` `row_counts`, export of 2026-11-23T18:34:47Z. |

## Reliability of ingest

Window: 2026-11-22T16:35Z to 2026-11-23T18:36Z, from the 1,565 raw manifest objects (read-only
sync of `manifests/boston/`, 2026-11-23).

| Measure | Value | Method |
| --- | --- | --- |
| Expected runs | 1,562 | Every minute from the first to the last scheduled run. |
| Missed runs | 1 (0.06 %) | Expected minutes with no manifest. |
| Duplicate deliveries | 3 extra runs, all in the minute 2026-11-22T16:35Z (0.19 % of minutes) | Manifest objects minus distinct scheduled minutes with a manifest. Two of the four runs of that minute started 0.35 s apart and shared every `capture_id`. The other two fetched 3 s and 40 s later. |
| Delay from scheduled minute to fetch | median 17.6 s, p95 19.4 s, max 53.4 s | `vehicle_positions` `fetched_at - scheduled_at` over the same 1,565 runs. |

Since the conditional manifest write (see ARCHITECTURE_DECISIONS.md, ADR-013), duplicate
deliveries are counted by `agg_pipeline_health.duplicate_delivery_count` and the hourly
`duplicate_deliveries` check. No duplicate has occurred since that change shipped: pending.

## Latency and cost

| Measure | Value | Method |
| --- | --- | --- |
| Capture to gold | pending | Needs several scheduled runs. By schedule alone (curate at :00/:15/:30/:45, transform at :20 plus about one minute) a capture is in gold between about 20 and 80 minutes after it is fetched; this is derived from the schedules, not measured. |
| Cost per 1,000 captures | $0.0086 on 2026-11-22 (day total $0.0116, 1,341 captures) | `gold/cost_daily.parquet`: Cost Explorer spend for resources tagged `project=headway` divided by captures. The first day is partial (deployment started mid-day) and Lambda usage is inside the free tier, so S3 dominates. |

## Arrival reconstruction

First pass on the 1-minute data of 2026-11-22T16:35Z to 2026-11-23T19:40Z (about 27 hours; read-only
copy of the curated vehicle positions and static tables), dbt models `int_vehicle_stop_events`
and `fct_reconstructed_arrivals`, built locally on 2026-11-23. Re-validation after 15 s sampling
ships is pending; these numbers describe 1-minute sampling.

| Measure | Value | Method |
| --- | --- | --- |
| Hand-check, 10 trips, 30 trip stops | median absolute difference 16.0 s (mean 17.0 s, max 44 s, 25 of 30 within 30 s) | Trips of 2026-11-23: Red 2, Orange 2, Blue 2, Green-B/C/D/E 1 each. Three stops per trip (an observed one, a bracketed one, a second observed one). For each, the raw vehicle rows within 170 s of the model's arrival were printed with the straight-line distance to the stop, and an arrival was read off them by hand (approach speed from the earlier rows, time to cover the remaining distance). One reader, no second opinion. |
| Hand-check, observed stops (20) | median 18.2 s, model earlier than the read by a median of 18.2 s | The model takes the midpoint of the interval; the read sits nearer the STOPPED_AT row. All 20 reads fall inside the model's interval. |
| Hand-check, bracketed stops (10) | median 5.6 s (signed +2.2 s), 9 of 10 within 30 s | One case 36 s later than the read: a vehicle that dwelled, which distance interpolation cannot see. |
| Confidence shares, all lines | high 13.1 %, medium 78.1 %, low 4.8 %, missing 4.0 % of 37,698 trip stops | `agg_arrival_reconstruction_daily` over the 27 hours; 2,357 trip instances. Medium dominates because most observed intervals are 31-120 s wide at 1-minute sampling. |
| Trips left out | ADDED trips: 1,365 trip instances (no schedule, no shape); 13 other observed trips not in the schedule | Same table, summed over lines. |
| Monotonic violations | 102 trip stops: Green-C 78, Green-D 14, Green-B 8, Orange 2 | Both stops of each pair are set to low. Not investigated further. |

The agreement is against one person's reading of the same raw rows, not against a ground truth such
as AVL or fare gate data, which this project does not have.
