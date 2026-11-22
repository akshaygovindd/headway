-- Prediction reliability per line, direction, service date and scheduled hour.
-- Rows exist only for hours inside the trip_updates observation window.
with scheduled as (
    select
        line,
        direction_id,
        service_date,
        hour_local,
        count(*) as coverage_denominator
    from {{ ref('int_scheduled_stop_times') }}
    group by line, direction_id, service_date, hour_local
),

eligible as (
    select
        line,
        direction_id,
        service_date,
        hour_local,
        count(*) as n_eligible,
        count(distinct (trip_instance_key, stop_sequence)) as coverage_numerator,
        median(predicted_deviation_s) as median_predicted_deviation_s,
        quantile_cont(predicted_deviation_s, 0.9) as p90_predicted_deviation_s,
        median(abs(revision_s)) as median_abs_revision_s
    from {{ ref('int_eligible_predictions') }}
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
    coalesce(e.coverage_numerator, 0) as coverage_numerator,
    s.coverage_denominator,
    100.0 * coalesce(e.coverage_numerator, 0) / s.coverage_denominator as coverage_pct,
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
