-- Target trip stops of the forecast served now (Release 3, ADR-019): for the serving prediction
-- time t and each horizon (15, 30, 60 minutes), the trip stops scheduled in the 15-minute slot
-- that starts a horizon after t. The columns are those of int_forecast_targets; there is no
-- arrival yet, so target_arrival_ts and label_delay_s are null. segment is the run of the trip
-- instance with the latest arrival known at t, the run whose upstream delay a training example
-- would use; null when the trip has none yet (no persistence value).
with trip_segment as (
    select
        u.trip_instance_key,
        arg_max(u.segment, u.arrival_ts) as segment
    from {{ ref('int_usable_arrivals') }} u
    cross join (select distinct prediction_ts from {{ ref('int_forecast_serving_grid') }}) g
    where u.known_at <= g.prediction_ts and abs(u.deviation_s) <= 3600
    group by u.trip_instance_key
)

select
    g.line,
    g.prediction_ts,
    g.service_date as prediction_service_date,
    g.hour_local,
    g.day_type,
    h.horizon_min,
    s.direction_id,
    s.stop_id,
    s.trip_instance_key,
    ts.segment,
    s.stop_sequence,
    s.scheduled_arrival_ts,
    cast(null as timestamp with time zone) as target_arrival_ts,
    cast(null as double) as label_delay_s
from {{ ref('int_forecast_serving_grid') }} g
cross join (select * from (values (15), (30), (60)) as t (horizon_min)) h
join {{ ref('int_scheduled_stop_times') }} s
    on s.line = g.line
    and s.scheduled_arrival_ts >= g.prediction_ts + to_minutes(h.horizon_min)
    and s.scheduled_arrival_ts < g.prediction_ts + to_minutes(h.horizon_min + 15)
left join trip_segment ts on ts.trip_instance_key = s.trip_instance_key
