-- Prediction deviation per line, direction, stop and service date, counting only
-- scheduled trip stops in hours inside the trip_updates observation window.
-- Coverage counts only observable trip stops; it counts covered trip stops (including SKIPPED); deviation uses eligible
-- predictions only.
with computed as (
with observed as (
    select service_date, hour_local
    from {{ ref('int_observation_window') }}
    where feed = 'trip_updates'
),

scheduled as (
    select
        s.line,
        s.direction_id,
        s.stop_id,
        s.service_date,
        count(*) as coverage_denominator
    from {{ ref('int_observable_trip_stops') }} s
    join observed w on w.service_date = s.service_date and w.hour_local = s.hour_local
    group by s.line, s.direction_id, s.stop_id, s.service_date
),

eligible as (
    select
        p.line,
        p.direction_id,
        p.stop_id,
        p.service_date,
        count(*) as n_eligible,
        median(p.predicted_deviation_s) as median_predicted_deviation_s
    from {{ ref('int_eligible_predictions') }} p
    join observed w on w.service_date = p.service_date and w.hour_local = p.hour_local
    group by p.line, p.direction_id, p.stop_id, p.service_date
),

stop_order as (
    select
        line,
        direction_id,
        stop_id,
        median(stop_sequence) as stop_sequence
    from {{ ref('int_scheduled_stop_times') }}
    group by line, direction_id, stop_id
),

covered as (
    select
        c.line,
        c.direction_id,
        c.stop_id,
        c.service_date,
        count(*) as coverage_numerator
    from {{ ref('int_covered_trip_stops') }} c
    join observed w on w.service_date = c.service_date and w.hour_local = c.hour_local
    group by c.line, c.direction_id, c.stop_id, c.service_date
)

select
    s.line,
    s.direction_id,
    s.stop_id,
    s.service_date,
    o.stop_sequence,
    e.median_predicted_deviation_s,
    coalesce(e.n_eligible, 0) as n_eligible,
    coalesce(c.coverage_numerator, 0) as coverage_numerator,
    s.coverage_denominator as observable_trip_stops,
    s.coverage_denominator,
    100.0 * coalesce(c.coverage_numerator, 0) / s.coverage_denominator as coverage_pct
from scheduled s
left join stop_order o
    on o.line = s.line
    and o.direction_id is not distinct from s.direction_id
    and o.stop_id = s.stop_id
left join eligible e
    on e.line = s.line
    and e.direction_id is not distinct from s.direction_id
    and e.stop_id = s.stop_id
    and e.service_date = s.service_date
left join covered c
    on c.line = s.line
    and c.direction_id is not distinct from s.direction_id
    and c.stop_id = s.stop_id
    and c.service_date = s.service_date
)

{{ history_union('agg_stop_deviation_daily') }}
