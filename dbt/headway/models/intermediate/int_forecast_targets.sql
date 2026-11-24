-- Target trip stops of the forecast examples (Release 3, ADR-019): for each prediction time t and
-- horizon (15, 30, 60 minutes), the usable arrivals (high or medium, nearest run, deviation within
-- an hour) scheduled in [t + horizon, t + horizon + 15 min), with the label label_delay_s. Hours of
-- t and of the target arrival that are a data outage are left out. Only the label and
-- target_arrival_ts come from after t; the features are added in fct_forecast_examples.
    select
        g.line,
        g.prediction_ts,
        g.service_date as prediction_service_date,
        g.hour_local,
        g.day_type,
        h.horizon_min,
        u.direction_id,
        u.stop_id,
        u.trip_instance_key,
        u.segment,
        u.stop_sequence,
        u.scheduled_arrival_ts,
        u.arrival_ts as target_arrival_ts,
        u.deviation_s as label_delay_s
    from {{ ref('int_forecast_grid') }} g
    cross join (select * from (values (15), (30), (60)) as t (horizon_min)) h
    join {{ ref('int_usable_arrivals') }} u
        on u.line = g.line
        and u.scheduled_arrival_ts >= g.prediction_ts + to_minutes(h.horizon_min)
        and u.scheduled_arrival_ts < g.prediction_ts + to_minutes(h.horizon_min + 15)
    where u.is_nearest_segment
        and abs(u.deviation_s) <= 3600
        and not u.data_outage
