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
  stop_id x service_date (median only). `agg_line_reliability_window`: line x window_label
  (`last_24h`, the 24 hours up to `metrics_as_of`, all directions): exact median and p90 over
  the window's eligible predictions, which the Overview reads instead of averaging hourly medians.
- **Numerator / denominator:** not a ratio. `n_eligible` is the number of eligible
  predictions behind the statistic.
- **Inclusion:** the eligible prediction of a trip stop is its last snapshot collected
  before the scheduled arrival that is schedule matched, from a non-duplicate capture,
  has a predicted arrival, and has `horizon_s` (`predicted_arrival_ts - collected_at`)
  between 0 and 1800 seconds.
- **Implausible matches:** a pick with `abs(predicted_deviation_s) > 3600`
  (`is_implausible_deviation`, `int_prediction_picks`) is left out of deviation and revision
  (`int_eligible_predictions`) but still counts toward coverage. The excluded trip stops are
  reported as `implausible_deviation_count` in `agg_line_reliability_hourly` and
  `agg_line_reliability_window`.
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
- **Grain:** line x direction_id x service_date x hour_local (`agg_line_reliability_hourly`);
  line x window_label in `agg_line_reliability_window` (exact median over the window).
- **Numerator / denominator:** not a ratio. The first snapshot of a trip stop has no prior
  and is left out of the median.
- **Inclusion / exclusion:** the same eligible predictions as metric 1.
- **Source models:** `fct_stop_predictions`, `int_eligible_predictions`.

## 3. Coverage

- **Business meaning:** the share of observable scheduled trip stops for which the feed
  gave us a usable observation. It measures feed completeness during observed periods,
  not service delivery and not pipeline uptime.
- **Formula:** `coverage_pct = 100 * coverage_numerator / coverage_denominator`.
- **Grain:** line x direction_id x service_date x hour_local; also per stop and service
  date in `agg_stop_deviation_daily`; line x window_label in `agg_line_reliability_window`
  (sums of the hourly numerators and denominators).
- **Numerator:** scheduled trip stops with at least one usable observation: a
  schedule-matched, non-duplicate snapshot whose relationship is not NO_DATA, collected
  0 to 30 minutes before the scheduled arrival (`int_covered_trip_stops`). For SKIPPED
  snapshots, which have no predicted time, the 30 minutes are measured from the scheduled
  arrival; for other snapshots `horizon_s` is used.
- **Denominator:** observable scheduled subway trip stops whose scheduled arrival falls in
  the hour (`int_observable_trip_stops`, column `observable_trip_stops`). A trip stop
  scheduled at T is observable when at least one ok, non-duplicate `trip_updates` capture
  has `fetched_at` in [T - 30 min, T]. `scheduled_trip_stops` reports all scheduled stops
  for comparison; the gap is time the pipeline was not capturing.
- **Outages.** Stops we could not have observed are left out of numerator and denominator.
  Pipeline gaps are reported by `missed_runs` and the freshness checks, not by coverage.
- **Skipped stops.** A SKIPPED stop is covered: it is a usable observation of a service
  change. It is reported separately as `skipped_stop_count` (covered trip stops whose
  latest usable snapshot is SKIPPED) and `skipped_share = skipped_stop_count /
  observable_trip_stops`. SKIPPED stops stay out of deviation and revision, so
  `coverage_numerator` can exceed `n_eligible`.
- **Exclusion:** NO_DATA, duplicate captures, ADDED trips (not in the schedule, so not in
  the denominator), unobservable trip stops, hours outside the observation window.
- **Source models:** `int_covered_trip_stops`, `int_observable_trip_stops`,
  `int_scheduled_stop_times`, `int_captures`,
  `int_observation_window`, `agg_line_reliability_hourly`.
- **Quality link:** `agg_quality_checks_hourly.prediction_coverage` is the hourly
  coverage over all lines, observable trip stops only (warns below 80, fails below 50).
  `schedule_join_coverage` is the share of non-added snapshots matched to the schedule among
  snapshots taken within 30 minutes before the stop's arrival (scheduled time when matched,
  predicted time otherwise); snapshots with no arrival time are not evaluable.

## 4. Freshness

- **Business meaning:** how old the data is when it reaches us, and whether the pipeline
  is delivering new data on time.
- **Formula:** `late_event_share = 100 * records with (fetched_at - event_ts) > 300 s /
  records with an event_ts`, over `trip_updates` and `vehicle_positions` rows of
  non-duplicate captures. The 5 minute bound is fixed. Alongside it,
  `missed_runs = expected_runs - distinct run_id`, and `duplicate_share` is the percentage
  of successful captures that repeat the previous payload. `duplicate_deliveries` counts, per
  hour, the extra scheduler deliveries of a minute (EventBridge Scheduler is at-least-once):
  `agg_pipeline_health.duplicate_delivery_count` is the number of runs recorded for the run's
  scheduled minute beyond the first, reported on the earliest kept run.
  `stg_manifests` keeps one row per `capture_id`: the earliest `fetched_at`, ties broken by the
  lowest `run_id`; duplicate-delivery records written by ingest are excluded from it.
- **Grain:** service_date x hour_local x check_name (`agg_quality_checks_hourly`); per run
  in `agg_pipeline_health`.
- **Numerator / denominator:** late records over records with an event time. Expected runs
  per hour are the minutes of that hour between the first and last scheduled run observed
  overall, so partial first and last hours do not report false misses.
- **Exclusion:** records without event_ts, duplicate captures (for lateness).
- **Source models:** `int_captures`, `stg_trip_updates`, `stg_vehicle_positions`,
  `agg_quality_checks_hourly`, `agg_pipeline_health`.
- **Thresholds:** seed `quality_thresholds` (warn / fail): missed_runs 3/10 per hour,
  duplicate_deliveries 1/5 per hour,
  duplicate_share 20/50 %, invalid_coordinate_share 0.5/5 %, missing_trip_id_share
  5/20 %, prediction_coverage 80/50 % (below), schedule_join_coverage 90/70 % (below),
  late_event_share 5/20 %, implausible_deviation_share 1/5 % (implausible picks over
  eligible plus implausible picks, per hour). `vehicle_count_drop` (25/50 %) is computed only when the same
  hour one week earlier was observed; otherwise its status is `no_baseline`.

## Known limitations

- Captures are sampled once a minute. A prediction can change between samples, so the
  last snapshot before arrival may be up to a minute stale, and revisions are measured
  between samples, not between feed updates.
- Predictions are not arrivals. Deviation compares a forecast with the timetable; it is
  not observed lateness.
- Only the 0 to 30 minute horizon is evaluated.
- MBTA heavy rail (Red, Orange, Blue) is operated largely by headway, not to a timetable,
  so deviation from the schedule is a weaker signal there than on light rail.
- Diversion service (service_id `DIV...`) changes the schedule baseline, so deviation
  during a diversion measures lateness against the diversion timetable.
- Release 2 adds headway adherence, which fits heavy rail better than schedule deviation.
- Subway routes only; added trips are excluded from schedule-based metrics.
- These are Headway approximations from public feeds, not official MBTA performance
  measures.
