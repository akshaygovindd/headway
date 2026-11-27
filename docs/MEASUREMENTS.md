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

### Transform run on 2026-11-24 (16:20 UTC slot)

| Step | 23 Nov | 24 Nov |
| --- | --- | --- |
| Total | 46 s | 137 s |
| `s3_load` | | 47 s |
| `dbt_build` | | 43 s |
| `dbt_test` | | 31 s |
| `export_gold` | | 3 s |
| `publish` | | 3 s |

The 23 Nov total is the manual run's; the 24 Nov run is the scheduled one with the 300 s
incremental trigger in effect, so the run covers more data per slot. Both are far below the 900 s
timeout.

## Transform duration with closed-day facts

### Production run of 2026-11-26 (watch item)

The first transform run with Release 2 on, in Lambda (900 s limit), on 1-minute vehicle data:

| Step | Seconds |
| --- | --- |
| `s3_load` | 109 |
| `dbt_build` | 230 |
| `dbt_test` | 69 |
| Total | 453 |

15-second vehicle sampling had just started, so vehicle rows grow about 4x as the 7-day window
fills. See [INCIDENTS](INCIDENTS.md#transform-duration-at-half-the-lambda-limit-caught-by-monitoring).

### Production runs after the incremental closed-day facts (2026-11-26 20:00 UTC to 2026-11-27 20:25 UTC)

Read-only from the CloudWatch log group `/aws/lambda/headway-transform` (the JSON summary line of
every run that logged `duration_ms`, 26 runs). Before: 453 s on 26 Nov (above), with Release 2 on
and every window day recomputed on each run. After the incremental deploy: facts of closed days are
loaded from `state/facts/` and only the open days are computed.

| Measure | Value |
| --- | --- |
| Duration, 25 of 26 runs | 161 s to 276 s (median of all 26 runs 244 s) |
| Duration, first runs after the deploy | 178 s (2026-11-26 20:19 UTC) and 172 s (20:23 UTC), against 453 s before |
| Duration, slowest run | 402 s (2026-11-27 03:27 UTC): the run also wrote `fct_headways` for 20 to 25 Nov (`dbt_build` 277 s). Cause: the fix for reused trip ids (one headway per trip stop, c71c50d, deployed from 324934d) bumped the `fct_headways` closed-day version to 2, so every persisted closed day was recomputed once; later runs load them from state |
| Memory used (`memory_used_pct`) | 61.8 % to 65.4 % of 3008 MB |
| Days computed per run | 2 or 3 (the open days); 9 in the 402 s run; the other closed days are loaded from state |
| Failed Lambda runs | none: every run logged `dbt_status` `success` or `tests_failed` |
| Runs with a failing dbt test | 6 of 26 (2026-11-27 04:23, 10:25, 11:25, 12:24, 13:25 and 14:25 UTC), always `data_invariants_r2`, which alarms (`headway-data-tests-failed`); the 6 runs since 15:25 UTC passed. The failing row was not investigated here |

All runs are far below the 900 s limit and the 600 s alarm. Runs grow during the day as the open day
accumulates 15-second vehicle data (for example 161 s at 04:23 UTC to 276 s at 06:25 UTC, and 197 s
at 12:24 UTC to 275 s at 17:25 UTC after the day closed and dropped out of the computed set), so
the figure to read is the range, not one run. The 453 s run and these ones are different loads (1-minute data
against the start of 15-second data), so this is not a like-for-like speed-up: it shows production
staying at 161 to 276 s with 15-second data and the window full, where the full recompute of the
earlier run was already at 453 s. The like-for-like comparison is the local one below (182.8 s
full rebuild, 102.4 s incremental steady state).

### Where `s3_load` goes (read-only, production bucket, 2026-11-26)

The window (`dt` from 7 days back to tomorrow, plus static GTFS) as the staging models read it:

| Measure | Value | Method |
| --- | --- | --- |
| Objects in the window | 786 (trip_updates 186, vehicle_positions 189, alerts 189, manifests 174, quarantine 20, static 28) | `list_objects_v2` per table and day, 45 calls in 2.3 s |
| Size | 73 MB (trip_updates 55 MB) | Same listing |
| One GET, serial | median 103 ms, p90 129 ms (from a laptop; in-region is lower) | 30 GETs one after the other |
| 60 GETs, 16 at a time | 0.63 s (6.1 s serial) | A thread pool of 16 |

DuckDB reads through httpfs on one thread, with a footer read and range reads for each object, so
its S3 time is the number of requests times their latency, one after the other. The transform now
fetches the window with one GET per object, 16 at a time, before the load (`curated_cache.py`).
Locally against moto (no latency) the 1,278 objects of the synthetic window (182 MB) take 1.6 s and
`s3_load` stays at 32 s: that part is CPU, mostly `stg_trip_updates` (a window function over 17 M
rows, 25 s). The production gain of the prefetch is therefore the request latency, which this
machine cannot reproduce; the next production run reports `curated_prefetch` (objects, bytes, ms)
and `step_ms.s3_load` to measure it.

### Release 4 Phase 4.5 tables: cost of the four new models

Measured on 2026-11-26 with `scripts/measure_transform.py` on the synthetic 7-day window with 4x
vehicle data (8 service days, 357,155 scheduled stop rows), the `headway-transform` image at 3008 MB
and 1.7 CPUs, Release 2 and 3 on, `LOOKBACK_DAYS=7`, a local moto S3, two invocations in a row.
"R4 off" is the same command without `HEADWAY_ENABLE_R4`; "R4 on" adds it (9 more tables exported:
the five of Phase 4.1 plus the trip-time budget, slow segments, the slow-zone comparison and the
accessibility outages).

| | R4 off | R4 on |
| --- | --- | --- |
| Duration, first invocation | 127.7 s | 142.4 s (+14.7 s, +11%) |
| Duration, second invocation | 125.9 s | 78.0 s |
| Peak memory (process) | 80.5% / 78.4% | 79.0% / 80.3% |
| Peak anonymous memory | 2254 MB | 2346 MB |
| `dbt_build` / `dbt_test`, first | 60.8 / 20.8 s | 71.5 / 22.3 s |
| Tables exported | 31 | 40 |
| Spill (peak) | 4526 MB | 4854 MB |

The like-for-like figure is the first invocation: R4 adds about 15 s and no measurable memory. The
second invocations differ because the R4-on pair reached its incremental steady state while the
baseline pair did not on this machine; do not read 78 s against 126 s as an R4 saving. Every run is
well under the 600 s alarm (`headway-transform-duration-high`, [RUNBOOK](RUNBOOK.md#alarm-headway-transform-duration-high))
and under the 85% memory budget of ADR-024. One dbt test fails in all runs, R4 on or off
(`unique_grain_agg_pipeline_health_run_id`): duplicate run ids from the date-shifted synthetic copies,
as in the earlier runs on this dataset; the sample and the real capture pass every test.

Why the trip-time budget stays bounded. It builds the ordered station pairs of each scheduled trip
and aggregates them to the grain in one query, never materialising them per day:

| Dataset | Scheduled pairs (transient) | Measured pairs | `agg_trip_time_budget` rows | Pairs with trips |
| --- | --- | --- | --- | --- |
| Real capture, 27 h (2 service days) | 787,270 | 281,859 | 31,906 | 26,056 |
| Synthetic 7 days, 4x (8 service days) | 3,329,102 | 576,263 | 32,561 | 13,028 |

The output depends on the line network (stations squared per line, direction, band and window), not
on how many days are read: 32 k rows either way. The transient pair set grows with days (about 0.4 M
per day) and is the part to watch; if a longer window makes it a problem, restrict the pairs to a
maximum number of stops apart or stage it per closed day like the facts of ADR-024. The other new
tables: `int_segment_travel_times` 49,897 rows (real) and 291,524 (synthetic), `agg_slow_segments`
3,002 and 3,031, `agg_accessibility_outages` 250 and 248.

The `make images-smoke` r4 run (sample data, Release 2 and 4 on, 3008 MB): 19.3 s (3.7 s load, 6.6 s
build, 8.7 s tests), 20.7% of the memory limit, 572 tests passed, 9 R4 tables exported.

### Full rebuild versus incremental closed days

Measured on 2026-11-26 with `scripts/measure_transform.py`: the `headway-transform` image under
Lambda conditions (read-only root, `--memory=3008m --memory-swap=3008m`, `--cpus=1.7`, a disk-backed
`/tmp`), Release 2 and 3 on, `LOOKBACK_DAYS=7`, a local moto S3. Dataset: the synthetic 7 days
ending today (the two real complete days date-shifted, vehicle positions x4 at 15 s offsets: 3.2 M
vehicle rows, 17.3 M trip updates, 5.7 M alerts), whose last day is a full day of data (an open
day, so more work than an hour into a real day). The MotherDuck publish is not exercised.

| Step (s) | Full rebuild | Incremental, first run (empty state) | Incremental, steady state |
| --- | --- | --- | --- |
| `s3_prefetch` | 1.6 | 1.8 | 1.6 |
| `s3_load` | 32.0 | 32.3 | 33.9 |
| `dbt_build` | 111.0 | 116.0 | 37.7 |
| `dbt_test` | 27.0 | 26.2 | 25.2 |
| `facts` (write closed days) | | 9.8 | 0.0 |
| `observability` | 6.0 | 5.9 | 0.1 |
| Everything else (start-up, state, export, forecast serving, cost, publish) | 5.2 | 4.7 | 3.9 |
| **Total** | **182.8** | **196.7** | **102.4** |
| Peak memory (process) | 1994 MB (66.3%) | 1923 MB (63.9%) | 1866 MB (62.0%) |
| Spill (peak) | 5525 MB | 5567 MB | 2484 MB |

"Full rebuild" is `HEADWAY_INCREMENTAL_FACTS=false`. "Steady state" is a second invocation on the
state the first one wrote: it computed the open days (26 and 27 Nov) and read 19 to 25 Nov from
`state/facts`. Before the prefetch and the facts, the same window took 176 s at 1882 MB (62.6%) on
the same settings, so the step above that moves with this change is `dbt_build`
(`fct_stop_predictions` 66 s to 12 s, `int_vehicle_stop_events` 17 s to 3 s). Both are well
under the 300 s and 85% targets in these conditions. Peak memory moves between runs of the same
code: the previous image peaked at 2546 MB (84.6%, window ending 25 Nov, no CPU limit) and 1882 MB
(62.6%) on the window above, so the full rebuild can sit close to the 85% budget; the incremental
runs measured 1866 to 1923 MB.

What this does not show: Lambda's own speed. This machine is faster than Lambda (the watch item
estimated 5x), so the steady-state 102 s is a floor for a production run, not a forecast; what
remains in it is `stg_trip_updates` (25 s, window-wide) and dbt's own overhead per invocation
(`dbt_test` is 25 s although its tests add up to 7 s). The first production run after the deploy
and `TransformDurationSeconds` give the real figure.

Incremental equals full: `tests/unit/test_incremental_equivalence.py` builds the whole project
twice and compares every table row for row, on `samples/boston` (one service day, all four facts
read from state) and on the 27 h real capture (2026-11-22 persisted, 2026-11-23 computed): no
difference, and a deliberately emptied partition is caught. Two columns of
`agg_service_disruptions` are left out of the comparison because two full builds already differ
there (the first and last affected station follow an unordered list in `int_alert_scopes`).

## Transform memory under the account cap

Measured on 2026-11-25 on a Mac (Docker VM, arm64) with `scripts/measure_transform.py`: the
`headway-transform` image under Lambda conditions (read-only root, `--ipc=none`, unprivileged user,
`--memory=3008m --memory-swap=3008m`, a disk-backed `/tmp`), Release 2 and 3 on, `LOOKBACK_DAYS=7`,
defaults of ADR-024 (`memory_limit` 1500 MB, 1 thread, spill cap 8 GB). S3 is a local moto server;
the MotherDuck publish step is not exercised (no token locally: it fails on the missing SSM
parameter after the summary is written, so its time and memory are not in these numbers).
Peak memory is the process high-water mark (`VmHWM`) and step peaks are reset per step. The budget is
85% of 3008 MB = 2557 MB.

| Dataset | Peak memory | Spill (peak) | Duration |
| --- | --- | --- | --- |
| (a) Real curated data copied read-only from S3 (2026-11-22 to 25, 2 complete days, 4 days of files; 2.3 M trip-update rows/day) | 2052 MB (68.2%) | 831 MB | 67 s |
| (b) Synthetic 7 days: the two real complete days date-shifted, vehicle positions x4 at 15 s offsets (3.2 M vehicle rows, 17.3 M trip updates, 5.7 M alerts) | 2331 MB (77.5%) | 4853 MB | 172 s |

Per step (peak MB / spill MB / seconds), (a) then (b): `s3_load` 2052 / 455 / 12 then 2331 / 2427 /
37; `dbt_build` 2052 / 831 / 33 then 2160 / 4853 / 101; `dbt_test` 1641 / 0 / 19 then 1964 / 917 /
28; every other step (forecast serving, incident summaries, export, history, state, cost,
observability, analyst examples) at most 673 MB and under 1.2 s in both.

How the limit was chosen (dataset b unless noted; Release 2 on):

| `memory_limit`, threads | Result |
| --- | --- |
| unbounded (6 GB, 4 threads), real data | killed by the 3008 MB cgroup limit after 5 s |
| 2200 MB, 2 threads, real data (Release 1 models) | 2917 MB (97%): over the budget |
| 1500 MB, 2 threads | `Out of Memory Error` in `int_vehicle_stop_events` and `fct_stop_predictions` |
| 1800 MB, 2 threads | 2572 MB (85.5%), spill 7.0 GB of 8 GB, 156 s: no margin |
| 1500 MB, 1 thread | 2214 MB (73.6%), spill 4.8 GB, 176 s |

The process adds about 700 to 800 MB to DuckDB's limit (Python, dbt, boto3, Elementary and
allocations DuckDB does not count), so a 2200 MB limit cannot meet the 85% budget, and the heavy
models need single-threaded operators to fit in 1500 MB. The Lambda's own `Max Memory Used` can read
higher than these local figures (INCIDENTS: ingest memory underestimated): check the first deployed
run against the alarm.

Headroom: the 7-day x4 window leaves 8 points to the budget and 3 GB of spill budget. The step that
grows with the window is `dbt_build` (spill 4.8 GB). A longer window or more feeds needs the
incremental closed-day design of ADR-015.

### Lambda REPORT `Max Memory Used` after the memory fix deploy

After the deploy, the Lambda `REPORT` line shows `Max Memory Used` of 2992 to 3008 MB while
`TransformMemoryUsedPct` reads 62 to 63% and runs succeed (140 to 220 s). Both are right: the REPORT
figure includes page cache from DuckDB spilling to `/tmp` (reclaimable: the kernel drops it under
pressure instead of killing the function), whereas the metric is the process RSS high-water mark.
The alarm deliberately uses process RSS, so a REPORT near 3008 MB alone is not a sign of trouble.

## Ingest memory and duration

Source: Lambda REPORT lines of `headway-ingest` after the 15 s polling deploy.

| Memory | Max memory used | Duration (single-poll run) |
| --- | --- | --- |
| 128 MB | 114 MB (89 %) | 4 to 6 s |
| 256 MB | 127 to 128 MB (50 %) | 2.5 s |

The local smoke (`make ingest-smoke`) measured 60 to 70 MB for the same handler because it
excludes the Lambda runtime's overhead.

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

### 72-hour window (Release 1 acceptance A2)

| Measure | Value | Method |
| --- | --- | --- |
| Missed minutes over 72 h | 0 of 4,321 minutes missed (0.00 %) over 72.0 h, A2 PASS | `make acceptance` (`scripts/acceptance.py`, test A2) run on 2026-11-27 at about 21:04 UTC and again at about 21:13 UTC, same result both times. A minute counts as seen when `agg_pipeline_health` in the public gold has a row with that `scheduled_at`; the window is the 72 h ending at the newest minute in the table (so it ends on 2026-11-27 about 21:00 UTC and starts on 2026-11-24). The limit is 1 %. |

The window ends with the newest minute in the table, so a later run covers a later 72 h; the
figure above is the one this run showed. Duplicate deliveries do not hide a gap: a minute counts
once. The earlier 26-hour window above (1 missed of 1,562) is a different, earlier measurement.

## Latency and cost

| Measure | Value | Method |
| --- | --- | --- |
| Capture to gold | pending | Needs several scheduled runs. By schedule alone (curate at :00/:15/:30/:45, transform at :20 plus about one minute) a capture is in gold between about 20 and 80 minutes after it is fetched; this is derived from the schedules, not measured. |
| Cost per 1,000 captures | $0.0199 on 2026-11-26 (day total $0.1022, 5,146 captures) | `gold/cost_daily.parquet`: Cost Explorer spend for resources tagged `project=headway` divided by captures. The first day is partial (deployment started mid-day) and Lambda usage is inside the free tier, so S3 dominates. |

## Arrival reconstruction

First pass on the 1-minute data of 2026-11-22T16:35Z to 2026-11-23T19:40Z (about 27 hours; read-only
copy of the curated vehicle positions and static tables), dbt models `int_vehicle_stop_events`
and `fct_reconstructed_arrivals`, built locally on 2026-11-23. Re-validation after 15 s sampling
ships is pending; these numbers describe 1-minute sampling. A comparison with MBTA's own times, at
scale and including the first four hours of 15 s sampling, follows below.

| Measure | Value | Method |
| --- | --- | --- |
| Hand-check, 10 trips, 30 trip stops | median absolute difference 16.0 s (mean 17.0 s, max 44 s, 25 of 30 within 30 s) | Trips of 2026-11-23: Red 2, Orange 2, Blue 2, Green-B/C/D/E 1 each. Three stops per trip (an observed one, a bracketed one, a second observed one). For each, the raw vehicle rows within 170 s of the model's arrival were printed with the straight-line distance to the stop, and an arrival was read off them by hand (approach speed from the earlier rows, time to cover the remaining distance). One reader, no second opinion. |
| Hand-check, observed stops (20) | median 18.2 s, model earlier than the read by a median of 18.2 s | The model takes the midpoint of the interval; the read sits nearer the STOPPED_AT row. All 20 reads fall inside the model's interval. |
| Hand-check, bracketed stops (10) | median 5.6 s (signed +2.2 s), 9 of 10 within 30 s | One case 36 s later than the read: a vehicle that dwelled, which distance interpolation cannot see. |
| Confidence shares, all lines | high 13.1 %, medium 78.1 %, low 4.8 %, missing 4.0 % of 37,698 trip stops | `agg_arrival_reconstruction_daily` over the 27 hours; 2,357 trip instances. Medium dominates because most observed intervals are 31-120 s wide at 1-minute sampling. |
| Trips left out | ADDED trips: 1,365 trip instances (no schedule, no shape); 13 other observed trips not in the schedule | Same table, summed over lines. |
| Monotonic violations, first pass | 102 trip stops: Green-C 78, Green-D 14, Green-B 8, Orange 2 | Both stops of each pair were set to low. Superseded by the fixes below. |

The agreement is against one person's reading of the same raw rows, not against a ground truth such
as AVL or fare gate data, which this project does not have.

### Reconstruction fixes (2026-11-23)

Same 27 hours and the same local rebuild as above, after two changes to `fct_reconstructed_arrivals`
and `int_vehicle_stop_events`: bracketed stops interpolate along a chain of known (distance, time)
points (observed midpoints and raw observations) that never goes back in time, and a vehicle whose
`current_stop_sequence` goes backwards on a trip id starts a new run (segment).

| Measure | Before | After | Method |
| --- | --- | --- | --- |
| Monotonic violations | 102 (Green-C 78, Green-D 14, Green-B 8, Orange 2) | 0 on every line | `agg_arrival_reconstruction_daily.n_monotonic_violations` summed over lines. |
| Reused trip ids | 49 vehicle-trip pairs whose stop sequence went backwards, merged into one run | 40 extra segments created (Green-E 15, Red 10, Green-D 6, Mattapan 5, Green-C 2, Green-B 1, Orange 1, Blue 0) | `n_segments_created`; the 49 vs 40 difference is runs with too few usable observations to reconstruct. |
| Trip stops | 37,698 | 37,781 | The extra segments add trip stops. |
| Confidence shares | high 13.1 %, medium 78.1 %, low 4.8 %, missing 4.0 % | high 13.1 %, medium 78.3 %, low 4.5 %, missing 4.1 % | Same table. |
| Hand-check, 30 trip stops | median 16.0 s, 25 of 30 within 30 s | unchanged: median 16.0 s, mean 17.0 s, max 44 s, 25 of 30 within 30 s | The same 30 trip stops, re-read from the rebuilt table against the same manual reads. Bracketed stops (10): median 5.6 s, signed +2.2 s, 9 of 10 within 30 s, as before. |

None of the 30 hand-check stops changed at all (checked row by row against the earlier build); the fixes change a few thousand rows
elsewhere, none of which the hand-check sampled.

### Release 2 metrics, first values (2026-11-23)

The same 27 hours, local rebuild, 1-minute sampling. Hours with a data outage are left out (2 of 29
hours here: the partial first and last hours, whose vehicle window is under 55 minutes). Values are
exact over the pooled rows except ETT and the deviation median, which average the per-day or
per-hour medians weighted by trip or arrival count. They describe 1-minute sampling and one
Sunday-to-Monday stretch; they are first values, not a benchmark.

| Line | Accuracy, median abs error 0-5 min | 10-20 min | Headways within 0.5-1.5× | ETT median (weekdays) | Arrival coverage (deviation) |
| --- | --- | --- | --- | --- | --- |
| Red | 23 s | 59 s | 67 % | +263 s | 32 % |
| Orange | 22 s | 32 s | 89 % | -41 s | 76 % |
| Blue | 23 s | 40 s | 87 % | +70 s | 85 % |
| Green-B | 23 s | 74 s | 72 % | -51 s | 74 % |
| Green-C | 23 s | 80 s | 77 % | -55 s | 76 % |
| Green-D | 21 s | 62 s | 78 % | -166 s | 76 % |
| Green-E | 23 s | 68 s | 77 % | -71 s | 77 % |
| Mattapan | 13 s | 85 s | 77 % | -76 s | 79 % |

Coverage uses one definition (see METRICS, shared rules): scheduled trip stops, headways or trips
in non-outage hours. Last 7 days, per line (accuracy is the mean over the four horizon buckets):

| Line | Accuracy | Actual deviation | ETT | Headways |
| --- | --- | --- | --- | --- |
| Red | 29 % | 32 % | 34 % | 29 % |
| Orange | 76 % | 76 % | 85 % | 75 % |
| Blue | 78 % | 85 % | 62 % | 82 % |
| Green-B | 63 % | 74 % | 64 % | 70 % |
| Green-C | 56 % | 76 % | 62 % | 72 % |
| Green-D | 69 % | 76 % | 76 % | 72 % |
| Green-E | 70 % | 77 % | 64 % | 72 % |
| Mattapan | 59 % | 79 % | 85 % | 76 % |

Red is below 50 % on every Release 2 metric, so the app withholds its values on every page; the
Red numbers in the table before this one are shown only so they are not hidden here. Accuracy is
limited by the reconstruction (about 18 s median error on observed stops), so differences under
about 20 s are not resolved. Headway pairs
dropped for a train in between or unproven order: 1,580 of 34,000 candidates (about 5 %).

The e2e sample is 59 minutes, so both of its hours are outages by the rule and the Release 2
aggregates in `tests/e2e/expected` show the exclusion path, not values; the unit fixtures cover the
statistics.

### Cross-check against MBTA LAMP (2026-11-26)

Service dates 2026-11-22 to 2026-11-26 (22 Nov from about 11:30 local, 26 Nov to the evening),
`fct_reconstructed_arrivals` rebuilt locally from a fresh read-only copy of production on
2026-11-26 (22:50 local), against MBTA's LAMP subway on-time-performance files (MassDOT Developers
License: use and redistribution allowed with MassDOT acknowledged; no LAMP rows are committed; the
26 Nov file is the complete day published at 04:12 UTC on 27 Nov). Difference = ours minus LAMP
`stop_timestamp`, matched by trip id and stop, then by nearest scheduled arrival, pairs more than
600 s apart rejected; 2 of our rows after LAMP's last timestamp left out. Method, licence, all
tables and limitations: [VALIDATION](VALIDATION.md); re-run with `make validate`.

| Measure | Value | Method |
| --- | --- | --- |
| Match rate | 97.9 % of 165,684 rows with an arrival time (high 99.5 %, medium 97.8 %, low 94.9 %) | Unmatched: bracketed Green-C/E stops LAMP has no row for, and reused trip ids. |
| Observed stops, ours moved to the interval end | median 0 s, 98.7 % within 5 s, 99.8 % within 30 s | 125,755 pairs. Both sides read the same STOPPED_AT report; LAMP's time is the end of our interval, ours its midpoint, so unshifted the bias is -(interval width / 2), median -26 s, and the share within 30 s is set by interval width, not by error. |
| By confidence, unshifted, median abs / p90 / within 30 s | high 10.5 s / 14.5 s / 99.9 %; medium 25.5 s / 45.5 s / 64.4 %; low 65.0 s / 119.5 s / 30.4 % | After the dwell correction below. Observed rows are identical before and after. |
| Before the dwell correction: bracketed stops | median bias +13.6 s (ours late), median abs 15.7 s, 81.4 % within 30 s (22-24 Nov); +12.4 s, 15.3 s, 82.6 % (25-26 Nov) | Interpolation lands inside the dwell. The bias followed LAMP's dwell at the stop: +2 s for dwells up to 15 s, +9 s for 16-30 s, +20 s for 31-60 s. |

MBTA's times come from the same realtime feed, so this is a cross-check by an independent
implementation, not ground truth. Conclusion: reconstruction reads the same stop reports as MBTA;
the -21 s bias of observed stops is the midpoint-versus-first-STOPPED_AT definition, not a bug;
bracketed arrivals were systematically about 13 s late, which the next entry corrects.

### Dwell correction of bracketed arrivals (2026-11-26)

`fct_reconstructed_arrivals` and `fct_headways` at version 3: a bracketed arrival of a run observed
about once a minute moves earlier by `along x E[dwell of a missed visit]` (`along`: how far the
stop lies between the two observations that straddle it), from the `reconstruction_dwell` seed. The
table is fitted on **22-24 Nov only, from our own observed STOPPED_AT data** (no LAMP data in the
model; the fit uses the share of visits with 0, 1 and 2 or more sightings per line, stop and hour
bucket, which identifies dwell where the median dwell of observed stops does not, because a short
dwell is less likely to be seen); **25-26 Nov are held out**. **Runs observed every 15 or 30 s are
not corrected** (no seed rows for those spacing classes): the correction is validated only at
1-minute sampling, and an earlier version that also corrected the first four hours of 15-second
data did not help there (within 30 s 84.2 % → 79.6 %). Same data before and after (the before
build has an empty seed). Observed arrivals, confidence counts and `known_at` are unchanged and
there are no monotonic violations. Before → after, ours minus LAMP, bracketed stops:

| Measure | In sample, 22-24 Nov (22,516 pairs) | Held out, 25-26 Nov, 1-minute (13,152 pairs) | Method |
| --- | --- | --- | --- |
| Median bias | +13.6 s → -3.3 s | +13.2 s → -4.1 s | `make validate`, bracketed rows, `By fit window and sampling period`. |
| Median absolute difference | 15.7 s → 6.6 s | 15.8 s → 7.0 s | Same. |
| p90 absolute difference | 37.4 s → 27.2 s | 36.6 s → 29.4 s | Same. |
| Within 30 s / within 60 s | 81.4 % → 90.9 % / 98.6 % → 98.1 % | 82.5 % → 90.3 % / 98.5 % → 97.9 % | Same. |
| 15-second period, held out (856 pairs) | not applicable | median bias +3.1 s → +2.9 s, within 30 s 84.5 % → 84.3 %, within 60 s 93.9 % → 93.9 % | Uncorrected: the 18 rows that differ are runs in the 90 s or more class. |
| Line cells only (no stop or hour) | | within 30 s 88.5 %, median abs 8.0 s (all held-out days) | Sensitivity, same split; the full hierarchy gives 89.9 %, and a shape bound of 6 to 40 gives 89.7 to 90.1 %. |

By line, held out, within 30 s before → after: Blue 84.8 → 99.5 %, Mattapan 98.9 → 99.5 %, Orange
95.1 → 98.3 %, Red 75.2 → 90.7 %, Green-B 76.9 → 87.7 %, Green-C 79.5 → 86.9 %, Green-D 73.2 → 82.3 %,
Green-E 77.8 → 81.5 %. Green-D and Green-E are the weakest, and within 60 s falls on them (Green-E
97.3 → 95.0 %, Green-D 97.4 → 95.9 %). All matched rows: median absolute 23.5 s → 22.0 s, within 30 s
67.2 % → 69.2 % (observed stops, 76 % of rows, are unchanged). Bracketed stops of 1-minute runs moved
by a median of 16 s (largest 46 s). Conclusion: the 13 s lateness is largely removed on days the
table was not fitted on (bias -4 s, 90 % within 30 s), with a small over-correction for short
dwells; 15-second data stays uncorrected until a refit on at least three full days of it, then
`make validate` again.

## Blue and Green-E deviation: late and early departures, not a matching offset

Blue shows a median actual deviation of +264 s (last 24 hours; +201 s over 7 days) while its ETT
is only about +60 s; Green-E shows -101 s with 35 % of arrivals on time. The check, on the same 27
hours:

- **The feed says the same.** The feed's own predictions, which do not depend on vehicle matching,
  give a median predicted deviation of +262 s for Blue and -86 s for Green-E over the last 24
  hours; the reconstruction gives +264 s and -101 s. At 5 minutes before arrival the prediction and
  the reconstruction of a trip agree within about 30 s (trip 78878754, 06:15: both put it 14
  minutes after the schedule at every stop).
- **No one-departure offset.** Trips are matched by `trip_id` and service date, not by order. Of
  the scheduled Blue trips inside the observed envelope 388 of 419 have a vehicle (266 of 286 for
  Green-E). In five hand-checked Blue trips the neighbours scheduled 4 to 9 minutes before and
  after all have vehicles, and their deviations differ per trip (Blue per-trip median: p10 -39 s,
  p90 +682 s), where a shift by one departure would move every trip by about one headway.
- **Late at departure, normal in running time.** Blue's deviation is the same 193 to 252 s at
  every stop of a trip and its ETT (second to second-to-last stop, minus the scheduled running
  time) is small, so the lateness is at the start of the trip.

Conclusion: real late Blue departures and early Green-E running against the timetable in this
stretch, not a defect of trip matching, so there is no code change or regression test. The
timetable is the Fall 2026 version D static feed; a pattern that persists over more days is worth
re-checking against the published schedule.

## Alert vs reality: hand check

*Pending: to be filled in by hand.* The matching of incidents to line-scope service alerts
(`docs/METRICS.md`, "Alert vs reality") is a rule: same line, overlap within 30 minutes. Whether it
agrees with a reader's judgement is measured here, not assumed.

Procedure: after a few weeks of incidents, run
`uv run python scripts/alert_handcheck.py --db <dbt duckdb of an R3 build> --out alert_handcheck.csv`
(at least 10 rows of each kind), read each row against the alert text and the incident's evidence, and
fill `reviewer_verdict` (`agree` / `disagree` / `unclear`) and `reviewer_note`.

| Kind | Rows reviewed | Agree | Disagree | Unclear | Notes |
| --- | --- | --- | --- | --- | --- |
| matched incident | pending | | | | |
| unmatched incident | pending | | | | |
| matched alert | pending | | | | |
| unmatched alert | pending | | | | |

Reviewed on: pending. Period covered: pending. Reviewer: pending.
