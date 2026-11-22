-- Prediction deviation per line, direction, stop and service date, counting only
-- scheduled trip stops in hours inside the trip_updates observation window.
with scheduled as (
    select
        s.line,
        s.direction_id,
        s.stop_id,
        s.service_date,
        count(*) as coverage_denominator
    from {{ ref('int_scheduled_stop_times') }} s
    join {{ ref('int_observation_window') }} w
        on w.service_date = s.service_date and w.hour_local = s.hour_local and w.feed = 'trip_updates'
    group by s.line, s.direction_id, s.stop_id, s.service_date
),

eligible as (
    select
        line,
        direction_id,
        stop_id,
        service_date,
        count(*) as n_eligible,
        median(predicted_deviation_s) as median_predicted_deviation_s
    from {{ ref('int_eligible_predictions') }} p
    where exists (
        select 1 from {{ ref('int_observation_window') }} w
        where w.service_date = p.service_date and w.hour_local = p.hour_local and w.feed = 'trip_updates'
    )
    group by line, direction_id, stop_id, service_date
)

select
    s.line,
    s.direction_id,
    s.stop_id,
    s.service_date,
    e.median_predicted_deviation_s,
    coalesce(e.n_eligible, 0) as n_eligible,
    s.coverage_denominator,
    100.0 * coalesce(e.n_eligible, 0) / s.coverage_denominator as coverage_pct
from scheduled s
left join eligible e
    on e.line = s.line
    and e.direction_id is not distinct from s.direction_id
    and e.stop_id = s.stop_id
    and e.service_date = s.service_date
