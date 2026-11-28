# Service level objectives

Release 2. Four objectives measure whether the pipeline did its job; they are model outputs, not
promises about the MBTA. `agg_slo_daily` holds one row per objective and service date,
`agg_slo_rollup` the last 7 and 30 service dates. Both are in the public gold export and on the
Pipeline health page, and `make slo-report` writes the current picture to
[SLO_REPORT.md](SLO_REPORT.md) from the published tables (HTTPS, no credentials). The models and
the section only exist when `HEADWAY_ENABLE_R2=true`.

| Objective | Target | One event is | A good event is |
| --- | --- | --- | --- |
| Capture completeness | at least 99 % of minutes | a minute of the service day, from the first capture | poll 0 of all three feeds was captured (`status = ok`) |
| Gold freshness | at least 99 % of hours | an hour of the service day, from the first recorded export | at the start of the hour the newest exported gold was at most 90 minutes old |
| Transform success | at least 99 % of scheduled runs | an hourly slot (the run scheduled for :20) | a scheduled run exported and published gold |
| Arrival coverage | at least 80 % of observable subway trip-stops during service hours | a scheduled subway trip stop in an hour with data | it has a high or medium reconstructed arrival (ADR-016) |

The targets and the 90 minutes live in the `slo_targets` seed.

## How each is measured

- **Day and window.** A service day runs 03:00 to 03:00 local. The measurement of a day ends at
  `slo_as_of`: the transform passes now minus 30 minutes, because curation runs every 15 minutes
  and the newest captures are not in the curated tables yet. A day in progress is therefore
  partial; the report and the page say so. A day is written to history once it closes (ADR-015),
  so the 30-day rollup keeps working beyond the 7-day build window.
- **Capture completeness** counts minutes from the first capture ever recorded to the end of the
  measurement. A minute with a failed or missing feed, or a run that never happened, is bad.
  Duplicate payloads are not failures: only the capture status matters. A minute's status for each
  feed is that of its poll-0 fetch, which every run makes for all three feeds; the extra 15 s
  vehicle polls are counted apart (`agg_pipeline_health.vehicle_polls_ok` and
  `vehicle_polls_error`, one per poll even when two polls returned the same payload), so a failed
  poll 3 does not make a minute bad and a missing poll 0 does.
- **Gold freshness** compares each hour start with the `gold_exported_at` of the runs recorded in
  `agg_transform_runs`. A single missed run leaves gold about 100 minutes old at the next top of
  the hour, which is a bad hour; an hourly schedule that works is 40 minutes old at the top of the
  hour. Hours before the first recorded export are not counted.
- **Transform success** needs the persisted run state (`state/transform_runs/`). A slot counts
  once its run has had time to be recorded; the run in progress is never in its own measurement,
  so it appears one run later. Manual runs and `rebuild_history` do not stand in for a scheduled
  slot. A run whose dbt tests failed but which exported and published gold is a good slot: the
  tests are reported by the quality checks, not by this objective.
- **Arrival coverage** is the coverage of `agg_actual_deviation_hourly`: reconstructed high or
  medium arrivals over scheduled trip stops of the hour, summed over lines. It leaves out hours
  with a data outage (those are counted against capture completeness, and counting them twice
  would hide which problem it was). Hours in which a line has an active line-scope detour, suspension or
  shuttle alert (the route, or two or more stations of it) are **not hidden**: they are reported as their own row,
  `arrival_coverage_disrupted`, with the same 80 % target, but they do not count toward the
  objective, because a train that is not running cannot be observed arriving. An alert on a single station (a closure) does not remove the line: only that station's trip stops are left out of the hours it is active (METRICS.md, stop-level flags).

## Error budget

For a target of *t* % over *N* events the budget is the number of bad events the target allows,
*(100 − t) / 100 × N*. `error_budget_used` is the bad events over that budget: 0 means no bad
events, 1 means the budget is spent exactly, above 1 means the objective is missed. A rollup sums
events over the window and computes the share and the budget from the sums, never from daily
shares.

## Reading a miss

Capture completeness and transform success are about the pipeline; gold freshness is about what
a reader sees; arrival coverage is about how much of the timetable the reconstruction can see
(vehicle sampling and trip matching), which is the one most likely to be below target in the
first weeks of 15 s sampling (see MEASUREMENTS.md). A miss is a prompt to look, not an incident:
the incident log (INCIDENTS.md) records what was done.
