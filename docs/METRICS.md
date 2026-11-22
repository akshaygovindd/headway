# Metrics

Release 1 reports four metrics for the subway routes of a city (Boston: Red, Orange,
Blue, Green-B/C/D/E, Mattapan). They are Headway approximations computed from public
GTFS and GTFS-realtime feeds. They are not official MBTA measures.

All times are UTC in storage; hours and service dates are local (America/New_York) with
a 03:00 service-day cutoff. A trip stop is one scheduled stop time of one trip instance
(`trip_instance_key` x `stop_sequence`).

## Shared rules

- **Observation window.** Aggregates only report hours present in `int_observation_window`:
  hours with at least one successful, non-duplicate `trip_updates` capture.
  `capture_minutes` counts distinct minutes with such a capture; `window_complete` is true
  at 55 or more. Coverage is never reported for hours the pipeline was not observing.
  Partial hours are reported with `window_complete = false`.
- **Duplicates.** A capture whose payload hash equals the previous successful capture of
  the same feed is a duplicate (`is_duplicate_capture`). Duplicate captures never count
  towards prior snapshots, eligible predictions, coverage or capture minutes.
- **Schedule match.** A prediction is matched when trip_id, service date, stop_sequence and
  stop_id equal a scheduled stop time (`schedule_matched`). Added trips are never matched.
- **Predictions are not arrivals.** Scheduled and predicted values live in separate
  columns. No metric treats a prediction as an observed arrival.

## 1. Predicted schedule deviation

- **Business meaning:** how far the feed's predicted arrival is from the timetable shortly
  before a train is due.
- **Formula:** `predicted_deviation_s = predicted_arrival_ts - scheduled_arrival_ts`
  (seconds; positive is late). Reported as the median and 90th percentile over eligible
  predictions.
- **Grain:** `agg_line_reliability_hourly`: line x direction_id x service_date x hour_local
  (hour of the scheduled arrival). `agg_stop_deviation_daily`: line x direction_id x
  stop_id x service_date (median only).
- **Numerator / denominator:** not a ratio. `n_eligible` is the number of eligible
  predictions behind the statistic.
- **Inclusion:** the eligible prediction of a trip stop is its last snapshot collected
  before the scheduled arrival that is schedule matched, from a non-duplicate capture,
  has a predicted arrival, and has `horizon_s` (`predicted_arrival_ts - collected_at`)
  between 0 and 1800 seconds.
- **Exclusion:** SKIPPED and NO_DATA relationships, ADDED trips (not schedule matched),
  duplicate captures, and hours outside the observation window.
- **Source models:** `fct_stop_predictions`, `int_eligible_predictions`,
  `int_scheduled_stop_times`, `int_observation_window`.

## 2. Prediction revision

- **Business meaning:** how much a prediction moves between snapshots; large revisions
  mean riders see unstable times.
- **Formula:** `revision_s = predicted_arrival_ts - prior_predicted_arrival_ts`, where the
  prior value is the previous non-duplicate snapshot of the same trip stop. Reported as
  the median of `abs(revision_s)`.
- **Grain:** line x direction_id x service_date x hour_local (`agg_line_reliability_hourly`).
- **Numerator / denominator:** not a ratio. The first snapshot of a trip stop has no prior
  and is left out of the median.
- **Inclusion / exclusion:** the same eligible predictions as metric 1.
- **Source models:** `fct_stop_predictions`, `int_eligible_predictions`.

## 3. Coverage

- **Business meaning:** the share of scheduled trip stops for which the feed gave us a
  usable observation. It measures data availability, not service delivery.
- **Formula:** `coverage_pct = 100 * coverage_numerator / coverage_denominator`.
- **Grain:** line x direction_id x service_date x hour_local; also per stop and service
  date in `agg_stop_deviation_daily`.
- **Numerator:** scheduled trip stops with at least one usable observation: a
  schedule-matched, non-duplicate snapshot whose relationship is not NO_DATA, collected
  0 to 30 minutes before the scheduled arrival (`int_covered_trip_stops`). For SKIPPED
  snapshots, which have no predicted time, the 30 minutes are measured from the scheduled
  arrival; for other snapshots `horizon_s` is used.
- **Denominator:** scheduled subway trip stops whose scheduled arrival falls in the hour
  (`int_scheduled_stop_times`).
- **Skipped stops.** A SKIPPED stop is covered: it is a usable observation of a service
  change. It is reported separately as `skipped_stop_count` (covered trip stops whose
  latest usable snapshot is SKIPPED) and `skipped_share = skipped_stop_count /
  coverage_denominator`. SKIPPED stops stay out of deviation and revision, so
  `coverage_numerator` can exceed `n_eligible`.
- **Exclusion:** NO_DATA, duplicate captures, ADDED trips (not in the schedule, so not in
  the denominator), hours outside the observation window.
- **Source models:** `int_covered_trip_stops`, `int_scheduled_stop_times`,
  `int_observation_window`, `agg_line_reliability_hourly`.
- **Quality link:** `agg_quality_checks_hourly.prediction_coverage` is the hourly
  coverage over all lines (warns below 80, fails below 50).

## 4. Freshness

- **Business meaning:** how old the data is when it reaches us, and whether the pipeline
  is delivering new data on time.
- **Formula:** `late_event_share = 100 * records with (fetched_at - event_ts) > 300 s /
  records with an event_ts`, over `trip_updates` and `vehicle_positions` rows of
  non-duplicate captures. The 5 minute bound is fixed. Alongside it,
  `missed_runs = expected_runs - distinct run_id`, and `duplicate_share` is the percentage
  of successful captures that repeat the previous payload.
- **Grain:** service_date x hour_local x check_name (`agg_quality_checks_hourly`); per run
  in `agg_pipeline_health`.
- **Numerator / denominator:** late records over records with an event time. Expected runs
  per hour are the minutes of that hour between the first and last scheduled run observed
  overall, so partial first and last hours do not report false misses.
- **Exclusion:** records without event_ts, duplicate captures (for lateness).
- **Source models:** `int_captures`, `stg_trip_updates`, `stg_vehicle_positions`,
  `agg_quality_checks_hourly`, `agg_pipeline_health`.
- **Thresholds:** seed `quality_thresholds` (warn / fail): missed_runs 3/10 per hour,
  duplicate_share 20/50 %, invalid_coordinate_share 0.5/5 %, missing_trip_id_share
  5/20 %, prediction_coverage 80/50 % (below), schedule_join_coverage 90/70 % (below),
  late_event_share 5/20 %. `vehicle_count_drop` (25/50 %) is computed only when the same
  hour one week earlier was observed; otherwise its status is `no_baseline`.

## Known limitations

- Captures are sampled once a minute. A prediction can change between samples, so the
  last snapshot before arrival may be up to a minute stale, and revisions are measured
  between samples, not between feed updates.
- Predictions are not arrivals. Deviation compares a forecast with the timetable; it is
  not observed lateness.
- Only the 0 to 30 minute horizon is evaluated.
- Subway routes only; added trips are excluded from schedule-based metrics.
- These are Headway approximations from public feeds, not official MBTA performance
  measures.
