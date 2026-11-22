-- Prediction reliability per line, direction, service date and scheduled hour.
-- Rows exist only for hours inside the trip_updates observation window.
-- Coverage counts only observable trip stops (a capture within 30 minutes before the
-- scheduled arrival), so pipeline outages appear in missed_runs, not in coverage.
-- Coverage (data availability) and deviation/revision (prediction accuracy) use
-- different populations: skipped stops are covered but have no deviation.
with scheduled as (
    select
        s.line,
        s.direction_id,
        s.service_date,
        s.hour_local,
        count(*) as scheduled_trip_stops,
        count(o.trip_instance_key) as observable_trip_stops
    from {{ ref('int_scheduled_stop_times') }} s
    left join {{ ref('int_observable_trip_stops') }} o
        on o.trip_instance_key = s.trip_instance_key and o.stop_sequence = s.stop_sequence
    group by s.line, s.direction_id, s.service_date, s.hour_local
),

eligible as (
    select
        line,
        direction_id,
        service_date,
        hour_local,
        count(*) as n_eligible,
        median(predicted_deviation_s) as median_predicted_deviation_s,
        quantile_cont(predicted_deviation_s, 0.9) as p90_predicted_deviation_s,
        median(abs(revision_s)) as median_abs_revision_s
    from {{ ref('int_eligible_predictions') }}
    group by line, direction_id, service_date, hour_local
),

covered as (
    select
        line,
        direction_id,
        service_date,
        hour_local,
        count(*) as coverage_numerator,
        count(*) filter (where is_skipped) as skipped_stop_count
    from {{ ref('int_covered_trip_stops') }}
    group by line, direction_id, service_date, hour_local
)

select
    s.line,
    s.direction_id,
    s.service_date,
    s.hour_local,
    e.median_predicted_deviation_s,
    e.p90_predicted_deviation_s,
    e.median_abs_revision_s,
    coalesce(e.n_eligible, 0) as n_eligible,
    coalesce(c.coverage_numerator, 0) as coverage_numerator,
    s.observable_trip_stops as coverage_denominator,
    s.observable_trip_stops,
    s.scheduled_trip_stops,
    100.0 * coalesce(c.coverage_numerator, 0) / s.observable_trip_stops as coverage_pct,
    coalesce(c.skipped_stop_count, 0) as skipped_stop_count,
    coalesce(c.skipped_stop_count, 0) / s.observable_trip_stops as skipped_share,
    w.capture_minutes,
    w.window_complete
from scheduled s
join {{ ref('int_observation_window') }} w
    on w.service_date = s.service_date and w.hour_local = s.hour_local and w.feed = 'trip_updates'
left join eligible e
    on e.line = s.line
    and e.direction_id is not distinct from s.direction_id
    and e.service_date = s.service_date
    and e.hour_local = s.hour_local
left join covered c
    on c.line = s.line
    and c.direction_id is not distinct from s.direction_id
    and c.service_date = s.service_date
    and c.hour_local = s.hour_local
where s.observable_trip_stops > 0
