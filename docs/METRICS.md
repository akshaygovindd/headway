# Metrics

Release 1 reports four metrics (1-4) for the subway routes of a city (Boston: Red, Orange,
Blue, Green-B/C/D/E, Mattapan). Release 2 adds reconstructed arrivals (5) and four metrics built on
them (6-9). They are Headway approximations computed from public GTFS and GTFS-realtime feeds.
They are not official MBTA measures.

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

## 5. Reconstructed arrivals (Release 2)

`fct_reconstructed_arrivals`, one row per trip instance, run (`segment`) and stop on the stretch
of the trip a vehicle was observed on. Observed arrivals are the midpoint between the last vehicle
position before the first STOPPED_AT at the stop and that STOPPED_AT; `interval_width_s` is the
width of that interval. Stops never seen STOPPED_AT are interpolated by distance along the shape
between the nearest known points either side (`bracketed`, `bracket_gap_s`): an observed stop's
midpoint or a raw observation. At equal distance the observed midpoint is the point after the
stop and the last raw observation at the stop the point before it. Known times never go back in
time along the shape and a stop's distance never decreases along `stop_sequence`, so bracketed
arrivals are monotonic by construction; `monotonic_ok = false` is left for two observed midpoints
out of order (both stops low).

A vehicle can run the same trip id again on the same day. When its `current_stop_sequence` goes
backwards on a trip id, a new run starts (`segment` 1, 2, ...), and distances, intervals and
brackets never cross runs. `agg_arrival_reconstruction_daily.n_segments_created` counts the extra
runs.

Confidence is high for an observed interval of 30 s or less, medium up to 120 s (observed) or for
a bracket gap up to 120 s, low otherwise. ADDED trips and trips without a shape are not
reconstructed and are counted in `agg_arrival_reconstruction_daily`. These are observed positions,
not official arrival times, and bracketing runs late when the vehicle dwelled at the stop (see
`docs/MEASUREMENTS.md`).

## Release 2 metrics: shared rules

The four metrics below (6-9) are computed from reconstructed arrivals.

- **High and medium arrivals only.** Low-confidence and missing arrivals never enter a value.
  They are used for one purpose: proving that no train sits between two others (metric 8).
- **Coverage beside every number.** Each aggregate carries the numerator and denominator of its
  coverage and the app shows it next to the value. The arrival coverage note is the high/medium
  share of reconstructed trip stops from `agg_arrival_reconstruction_daily`.
- **Data outage hours (`data_outage`).** An hour is an outage when `missed_runs` is 3 or more in
  that hour (the warn threshold of the `quality_thresholds` seed, read from the seed, not
  hard-coded) or when the `vehicle_positions` window of the hour is incomplete
  (`int_observation_window.window_complete` false, fewer than 55 capture minutes). Outage hours
  are excluded from every value, labelled `data_outage`, and counted (`n_excluded_outage`).
  A single missed run does not make an outage.
- **Service disruption (`service_disruption`).** An hour is labelled service disruption for a
  line when `agg_active_alerts` has an alert for that line active in that hour whose `effect` is
  `DETOUR`, `NO_SERVICE`, `REDUCED_SERVICE`, `SIGNIFICANT_DELAYS` or `MODIFIED_SERVICE`, or whose
  header text matches `shuttle` or `suspen` (case-insensitive). `ACCESSIBILITY_ISSUE`,
  `STOP_MOVED` and every other effect do not count. Disruption hours are kept, not excluded: the
  aggregates carry the label so a reader can separate them. The GTFS-realtime effect list has no
  SHUTTLE or SUSPENSION value, hence the text match.
- **Observation window.** Hours outside the `vehicle_positions` observation window have no rows.

## 6. Prediction accuracy (Release 2)

- **Business meaning:** how close the feed's predicted arrival is to when the train actually
  arrived, by how far ahead the prediction was made.
- **Formula:** `error_s = predicted_arrival_ts - arrival_ts` (seconds; positive means the
  prediction was later than the reconstructed arrival). Reported per horizon bucket as the
  median of `abs(error_s)`, the mean (MAE), and the share of errors within ±60 s and ±120 s.
- **Horizon and buckets:** `horizon_s = predicted_arrival_ts - collected_at`, the lead time a
  rider saw (it does not use the reconstructed arrival). Buckets 0-5, 5-10, 10-20 and 20-30
  minutes, each including its lower bound; 1,800 s belongs to 20-30.
- **One snapshot per bucket per trip stop:** the last non-duplicate, schedule-matched snapshot
  in the bucket that is not SKIPPED or NO_DATA, has a predicted arrival, and was collected at or
  before the reconstructed arrival. A trip stop can have up to four, one per bucket.
- **Grain:** `fct_prediction_errors`: trip_instance_key x segment x stop_sequence x
  horizon_bucket. `agg_prediction_accuracy_daily`: line x service_date x horizon_bucket x
  service_disruption.
- **Numerator / denominator (coverage):** trip stops with an error in the bucket over high or
  medium reconstructed trip stops of the line and day (`coverage_numerator`,
  `coverage_denominator`, `coverage_pct`).
- **Inclusion:** reconstructed arrivals of high or medium confidence. When a trip id was reused
  (several segments for one trip stop), the segment whose arrival is closest to the scheduled
  arrival is used.
- **Exclusion:** low-confidence and missing arrivals, ADDED trips, duplicate captures, data
  outage hours (counted in `n_excluded_outage`, hour of the reconstructed arrival).
- **Source models:** `fct_stop_predictions`, `fct_reconstructed_arrivals`, `int_hour_flags`.
- **Limitations:** the truth is a reconstruction with its own error (about 18 s median for
  observed stops, up to 120 s for medium confidence), so errors under about a minute are not
  resolved. Predictions are sampled once a minute, so the last snapshot of a bucket can be up to
  a minute stale. Arrivals are only reconstructed for vehicles observed on a shape.
- **Label:** "Prediction accuracy against reconstructed arrivals. A Headway approximation, not an
  official MBTA measure."

## 7. Actual schedule deviation (Release 2)

- **Business meaning:** how far trains really ran from the timetable, as opposed to metric 1,
  which compares a prediction with the timetable.
- **Formula:** `actual_deviation_s = arrival_ts - scheduled_arrival_ts` (positive is late).
  Reported as the median, the 90th percentile, and the share of arrivals within -1 minute and
  +5 minutes of schedule (-60 to +300 s inclusive).
- **Grain:** `agg_actual_deviation_hourly`: line x direction_id x service_date x hour_local (hour
  of the scheduled arrival, as in metric 1).
- **Numerator / denominator (coverage):** high or medium reconstructed trip stops over scheduled
  trip stops of the hour (`n_arrivals`, `scheduled_trip_stops`, `coverage_pct`).
- **Inclusion:** high or medium reconstructed arrivals that match a scheduled stop. Implausible
  deviations (more than 3600 s) are kept and counted in `n_implausible`: a reconstruction that
  far from the timetable is more likely a wrong trip match than a train that late, so they are
  excluded from the statistics.
- **Exclusion:** low and missing arrivals, ADDED and unscheduled trips, data outage hours (the
  row is kept with `data_outage = true` and empty values).
- **Source models:** `fct_reconstructed_arrivals`, `int_scheduled_stop_times`,
  `int_observation_window`, `int_hour_flags`.
- **Limitations:** heavy rail (Red, Orange, Blue) runs to headways, so the timetable is a weak
  reference there; metric 8 fits it better. Diversion service changes the baseline.
- **Label:** "Actual deviation from the timetable, from reconstructed arrivals. A Headway
  approximation, not an official MBTA measure."

## 8. Headway adherence (Release 2)

- **Business meaning:** whether trains come at the spacing the timetable promises, and how
  often they bunch or leave a gap.
- **Formula:** for two consecutive trains A then B at a stop, `actual_headway_s =
  arrival_B - arrival_A`; `scheduled_headway_s` is B's scheduled arrival minus the previous
  scheduled arrival at that stop of the same line and direction on the service date;
  `ratio = actual / scheduled`. Within band is `0.5 <= ratio <= 1.5`, bunching is `ratio < 0.5`,
  a gap is `ratio > 1.5`. Reported as the share of headways in each.
- **Grain:** `fct_headways`: line x direction_id x service_date x stop_id x to_trip_instance_key
  x to_segment. `agg_headway_adherence_hourly`: line x direction_id x service_date x hour_local
  (hour of the later train's arrival), over all stops of the line.
- **Numerator / denominator (coverage):** valid headways over scheduled headways of the hour
  (scheduled stop times with a predecessor at their stop). Dropped headways are counted in
  `n_dropped` and by reason.
- **When a pair is valid (no train in between):** A and B must be consecutive among the high and
  medium arrivals of the line, direction and stop, and the order must be proven by the vehicles
  at the neighbouring stops: (1) no other train of the line and direction has an arrival time
  (any confidence) between A and B at the stop itself; (2) at the previous and the next stop of
  the trip, wherever both A and B have an arrival time (any confidence), no other train has one
  between them. At least one neighbouring stop must prove it. A train that has a row at the stop
  but no time there (missing) is caught by (2).
- **Dropped headways and reasons:** `train_between` (check 1 or 2 failed, a train in between),
  `unproven_order` (no neighbouring stop where both trains have a time), `no_scheduled_headway`
  (B is the first scheduled train at the stop), `implausible` (actual headway over 3 hours or
  scheduled headway over 2 hours, such as a day boundary).
- **Exclusion:** low and missing arrivals as headway ends, ADDED trips, data outage hours.
- **Source models:** `fct_reconstructed_arrivals`, `int_scheduled_stop_times`, `int_hour_flags`.
- **Limitations:** a train never observed anywhere cannot be seen, so a gap can hide an
  unreconstructed train; the proof only covers trains that were reconstructed somewhere. Green
  line branches share the trunk, and each branch is measured against its own schedule. Arrival
  error of 20-120 s matters little for 4-10 minute headways, but ratios are coarse on short ones.
- **Label:** "Headway adherence from reconstructed arrivals. A Headway approximation, not an
  official MBTA measure."

## 9. ETT approximation (Release 2)

- **Business meaning:** how much longer a trip takes, end to end, than the timetable says: an
  approximation of excess trip time.
- **Formula:** per trip, `ett_s = (arrival_at_second_to_last_stop - arrival_at_second_stop)
  - (scheduled_arrival_at_second_to_last_stop - scheduled_arrival_at_second_stop)`. Stops are
  the second and second-to-last stops of the scheduled trip (`stop_sequence` rank). Positive is
  slower than schedule. Reported as the median and the 90th percentile.
- **Time bands:** by the scheduled arrival at the second stop in local time: AM peak 06:30-09:00,
  midday 09:00-15:30, PM peak 15:30-18:30, evening 18:30-24:00. Trips outside these hours are
  left out and counted (`n_outside_bands`). `day_type` (weekday or weekend) separates the days:
  the peaks are weekday periods, and weekend rows use the same clock bands.
- **Grain:** `agg_ett_daily`: line x direction_id x service_date x time_band x day_type.
- **Numerator / denominator (coverage):** trips with both end stops at high or medium over
  scheduled trips of the band (`n_trips`, `n_scheduled_trips`, `coverage_pct`).
- **Inclusion:** trips whose two stops are both reconstructed in the same run with high or
  medium confidence.
- **Exclusion:** trips observed on part of the line only, ADDED trips, data outage hours (hour
  of the second stop), trips outside the bands.
- **Source models:** `fct_reconstructed_arrivals`, `int_scheduled_stop_times`, `int_hour_flags`.
- **Limitations:** this is not the MBTA's measure of excess trip time, which compares against
  rider-weighted wait and travel using fare data; Headway has none of that. The two stops are
  chosen to avoid terminal dwell. A trip is counted only when a vehicle was seen at both stops,
  which favours well-observed trips.
- **Label:** "ETT approximation, adapted from the excess trip time idea. Not an official
  MBTA measure."

## Known limitations

- Trip updates are sampled once a minute. A prediction can change between samples, so the
  last snapshot before arrival may be up to a minute stale, and revisions are measured
  between samples, not between feed updates. Vehicle positions are sampled every 15 s
  (ADR-014) once that is deployed.
- Predictions are not arrivals. Deviation compares a forecast with the timetable; it is
  not observed lateness.
- Only the 0 to 30 minute horizon is evaluated.
- MBTA heavy rail (Red, Orange, Blue) is operated largely by headway, not to a timetable,
  so deviation from the schedule is a weaker signal there than on light rail.
- Diversion service (service_id `DIV...`) changes the schedule baseline, so deviation
  during a diversion measures lateness against the diversion timetable.
- Headway adherence (metric 8, Release 2) fits heavy rail better than schedule deviation.
- Subway routes only; added trips are excluded from schedule-based metrics.
- These are Headway approximations from public feeds, not official MBTA performance
  measures.
