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
