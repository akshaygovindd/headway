# Data quality: invariants of the data

The structural dbt tests (`not_null`, `unique_grain`, `accepted_range`) say the SQL ran. The tests
below say the numbers make sense: they read the real models and fail when a value is impossible
(**error**) or merely odd (**warn**). They run in every build (`make dbt`, `make e2e`) and in the
transform's `dbt test` step against production data.

What a failure does in production: the run still exports and publishes gold (a failing test never
skips a model) and is recorded with status `tests_failed` and `tests_failed` in
`agg_transform_runs`. The run's JSON summary line lists the names under `failed_tests` (error
severity) and `warned_tests` (warn severity). The transform emits the metrics `DataTestsFailed` and
`DataTestsWarned` (EMF, namespace `Headway`, zero on a clean run). **An error-severity failure
alarms**: `headway-data-tests-failed` (at least 1 in the hourly run) notifies the `headway-alerts`
topic ([RUNBOOK](RUNBOOK.md#alarm-headway-data-tests-failed)). **Warn severity never alarms**: it is
in the summary and the metric only. The slot still counts as a good one for the transform objective
(SLO.md): the alarm, not the objective, reports a failing data test. Tests are tagged `r2`, `r3` or
`r4` like the models they read, so a deployment with a release off skips them.

Units are those of METRICS.md: `pct_*`, `share_*` (reconstruction) and every `coverage_pct` are
0-100; `skipped_share` is the one 0-1 fraction.

| Invariant | Test | Severity | Notes |
|---|---|---|---|
| Every stop or station in a line's output is on that line in the static GTFS (route to trips to stop times, every feed version) | `line_stops_belong_to_line` on `fct_reconstructed_arrivals.stop_id`, `agg_stop_deviation_daily.stop_id`, `agg_marey_last_hour.stop_id`, `agg_headway_segment_window` and `agg_slow_segments` (from and to stop), `agg_trip_time_budget` (from and to station); reference `int_line_stops` | error | |
| Shares are inside their unit range | `data_invariants_{base,r2,r4}` | error | Columns found by name: `pct_*`, `share_*`, `*coverage_pct`, `target_pct`, `actual_pct` in 0-100; `skipped_share` in 0-1. A new mart column named like these is covered without editing yml. |
| Counts are not negative | `data_invariants_*` | error | `n_*`, `*_count`, coverage numerator and denominator, scheduled and observable trip stops |
| Median is not above p90 or p95, p90 not above p95, where both exist | `data_invariants_*` | error | Pairs found by name (`median_x` with `p90_x`, `p95_x`; `median_s` with `p95_s`) |
| `coverage_pct` is its own row's numerator over denominator, and the numerator never exceeds the denominator | `data_invariants_*` | error | A line-wide value copied onto a row fails. Tolerance 0.06 points (rounding). |
| No reconstructed arrival at a station inside an active line-scope shuttle or detour stretch, in an hour the alert named that station | `no_arrivals_in_shuttle_stretch` | error | Effects `DETOUR` and `NO_SERVICE`, or header text matching shuttle or suspension. Delay and reduced-service alerts are left out: trains still run. |
| Window marts cover exactly the stated window | `window_marts_consistent` | error | Only labels `last_24h` and `last_7d`; never more hours than the window holds (fewer is fine: no overnight service, outages); `last_24h` inside `last_7d`; headway and reliability window counts equal the sum of their hours. The actual-deviation window is at least its hourly sum, and equal for a line with no stop-level alert in the window (the hourly table leaves those stops out, the window does not: METRICS.md "Stop-level flags"). |
| A withheld row carries a reason and no value; a row that is not withheld carries no reason | `withheld_rows_consistent` (weather comparison); the API checks the rule for coverage-withheld rows in `tests/unit/test_web_fixtures.py` over every API answer of the real snapshot | error | |
| Medians of deviation beyond 30 minutes, headway median ratio outside 0.1-10, a percentage of exactly 0 or 100 over 200 or more observations | `data_suspicious_{r2,r4}` | warn | Real service can do this (a long shuttle hour, a very quiet line); the row is worth a look, not a stop. |

## Bugs these tests found on production data

- **Headway coverage above 100%** (`agg_headway_adherence_hourly`, Mattapan, 22 Nov, hour 20:
  39 valid headways over 38 scheduled). `fct_headways` made one headway for every run of a reused
  trip id at a trip stop, while the schedule has one headway per trip stop and the deviation metrics
  already use only the run nearest the schedule. `fct_headways` now keeps one row per trip stop,
  from the run nearest the schedule, and its closed-day version is 2 (`facts_state.FACT_VERSIONS`),
  so persisted days are recomputed (RUNBOOK "Closed-day facts").
- **Countdown coverage above 100%** (`agg_countdown_accuracy_window`, Blue, last_24h; production
  `data_invariants_r2` failed in 7 hourly runs on 27 Nov, the worst Beachmont: 284 countdowns over
  283 scheduled trip stops, 100.35%). The numerator counted countdowns by the hour of the predicted
  arrival, the denominator counts trip stops by the scheduled hour; Blue runs at about 99% coverage,
  so a train late across the window's first hour (its trip stop is scheduled outside the window,
  its countdown is scored inside) pushed the ratio over 100. The coverage numerator is now placed
  by the scheduled hour like the denominator (`fct_countdown_outcomes.scheduled_hour_local`); n and
  the accuracy shares keep the predicted-hour window.
- **The same mismatch in three more models** (found by auditing every ratio, share and coverage
  column; not seen in production data, which had not tripped the test). `agg_accuracy_window`
  counted its coverage numerator by the arrival hour of the error, `agg_prediction_accuracy_daily`
  by the arrival hour's outage flag and service-disruption label, and `agg_countdown_accuracy_daily`
  by the predicted service date and hour, while each denominator counts scheduled trip stops by the
  scheduled hour (and date). Each numerator is now placed by the scheduled hour like the
  denominator (`fct_prediction_errors.scheduled_hour_local`); n and the accuracy shares keep the
  arrival or predicted hour. The two daily tables are history, so the history families are at
  metric version 4 (DEPLOY, Sunday step C). The other models with a numerator over a denominator
  (deviation, headway, ETT, reliability, stop deviation, slow segments, trip-time budget, SLO,
  reconstruction shares, quality checks) count both sides from one set of rows or by the scheduled
  hour, and are unchanged.
- **Median above its own p95** (`agg_trip_time_budget`, 333 rows): `p95_s` was rounded to 0.1 s and
  `median_s` was not, so a median of 101.84 sat above a p95 of 101.8 when the trips were almost
  identical. `median_s` is rounded the same way.
