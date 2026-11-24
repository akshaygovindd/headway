-- Training examples for arrival-delay forecasts (Release 3, ADR-019). Internal: not exported.
--
-- An example is a trip stop that a forecaster standing at prediction time t is asked about: the
-- trip stop is scheduled to arrive in the 15-minute slot [t + horizon, t + horizon + 15 min) for a
-- horizon of 15, 30 or 60 minutes, and its arrival was reconstructed with high or medium
-- confidence (nearest run, deviation within an hour). The label is that arrival's delay,
-- label_delay_s = reconstructed arrival minus scheduled arrival, in seconds. Prediction times are
-- every quarter hour of the service hours (int_forecast_grid). An example is dropped when the hour
-- of t or the hour of the target arrival is a data outage (int_hour_flags).
--
-- Every other column is a feature known at t and nothing else:
--   stop_position, hour_local, day_type, scheduled_headway_s   schedule only
--   last_upstream_delay_s   delay of the same run at its latest earlier stop with known_at <= t
--   rolling_median_delay_30m / _60m, headway_within_share, active_disruption_alert,
--   skipped_share_1h        line features (int_forecast_line_features), all with known_at or
--                           collected_at or fetched_at <= t
--   mbta_predicted_deviation_s   MBTA's own predicted deviation for this trip stop from the latest
--                           snapshot with collected_at <= t: a feature, never the label, and the
--                           reference line of a forecast report, not a baseline
-- The target rows come from int_forecast_targets, kept apart so the leakage check can cut every
-- feature input at t and rebuild this table with the same targets.
-- known_at, not arrival_ts, decides what has been seen: an arrival belongs to a feature only once
-- the capture that determines it exists. The label is, by design, the only column from after t.
with targets as (
    select * from {{ ref('int_forecast_targets') }}
),

upstream as (
    select
        t.line, t.prediction_ts, t.horizon_min, t.trip_instance_key, t.segment, t.stop_sequence,
        arg_max(a.deviation_s, a.stop_sequence) as last_upstream_delay_s
    from targets t
    join {{ ref('int_usable_arrivals') }} a
        on a.trip_instance_key = t.trip_instance_key
        and a.segment = t.segment
        and a.stop_sequence < t.stop_sequence
        and a.known_at <= t.prediction_ts
    where abs(a.deviation_s) <= 3600
    group by 1, 2, 3, 4, 5, 6
),

positions as (
    select
        trip_instance_key,
        stop_sequence,
        dist_m / nullif(max(dist_m) over (partition by trip_instance_key), 0) as stop_position
    from {{ ref('int_trip_stop_distances') }}
),

scheduled as (
    select
        trip_instance_key,
        stop_sequence,
        epoch(scheduled_arrival_ts) - epoch(lag(scheduled_arrival_ts) over (
            partition by line, direction_id, service_date, stop_id
            order by scheduled_arrival_ts, trip_instance_key
        )) as scheduled_headway_s
    from {{ ref('int_scheduled_stop_times') }}
),

snapshots as (
    select trip_instance_key, stop_sequence, collected_at, predicted_deviation_s
    from {{ ref('fct_stop_predictions') }}
    where not is_duplicate_capture and schedule_matched and predicted_deviation_s is not null
)

select
    t.line,
    t.direction_id,
    t.stop_id,
    t.prediction_ts,
    t.horizon_min,
    t.trip_instance_key,
    t.segment,
    t.stop_sequence,
    t.prediction_service_date as service_date,
    t.hour_local,
    t.day_type,
    p.stop_position,
    sc.scheduled_headway_s,
    u.last_upstream_delay_s,
    f.rolling_median_delay_30m,
    f.rolling_median_delay_60m,
    f.headway_within_share,
    f.active_disruption_alert,
    f.skipped_share_1h,
    m.predicted_deviation_s as mbta_predicted_deviation_s,
    t.scheduled_arrival_ts,
    t.target_arrival_ts,
    t.label_delay_s
from targets t
left join upstream u
    on u.line = t.line and u.prediction_ts = t.prediction_ts and u.horizon_min = t.horizon_min
    and u.trip_instance_key = t.trip_instance_key and u.segment = t.segment and u.stop_sequence = t.stop_sequence
left join positions p on p.trip_instance_key = t.trip_instance_key and p.stop_sequence = t.stop_sequence
left join scheduled sc on sc.trip_instance_key = t.trip_instance_key and sc.stop_sequence = t.stop_sequence
left join {{ ref('int_forecast_line_features') }} f on f.line = t.line and f.prediction_ts = t.prediction_ts
asof left join snapshots m
    on m.trip_instance_key = t.trip_instance_key and m.stop_sequence = t.stop_sequence
    and t.prediction_ts >= m.collected_at
