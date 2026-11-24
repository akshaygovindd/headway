{#
  The feature SQL of the forecast (Release 3, ADR-019), shared by training and serving so that a
  feature is computed by the same code in both. Training: int_forecast_line_features and
  fct_forecast_examples over int_forecast_grid / int_forecast_targets. Serving:
  int_forecast_serving_features over the serving grid and targets (trip stops scheduled in the
  slots, not yet arrived: their label columns are null). The models' comments describe the
  columns; scripts/serving_parity.py holds that both give the same features for the same t.
#}
{% macro forecast_line_features(grid) %}
with arrivals as (
    select line, arrival_ts, known_at, deviation_s
    from {{ ref('int_usable_arrivals') }}
    where is_nearest_segment and abs(deviation_s) <= 3600
),

delays as (
    select
        g.line,
        g.prediction_ts,
        median(a.deviation_s) filter (where a.arrival_ts > g.prediction_ts - interval 30 minute) as rolling_median_delay_30m,
        count(a.deviation_s) filter (where a.arrival_ts > g.prediction_ts - interval 30 minute) as n_arrivals_30m,
        median(a.deviation_s) as rolling_median_delay_60m,
        count(a.deviation_s) as n_arrivals_60m
    from {{ grid }} g
    join arrivals a
        on a.line = g.line
        and a.known_at <= g.prediction_ts
        and a.arrival_ts <= g.prediction_ts
        and a.arrival_ts > g.prediction_ts - interval 60 minute
    group by g.line, g.prediction_ts
),

headways as (
    select
        g.line,
        g.prediction_ts,
        count(*) as n_headways_60m,
        count(*) filter (where h.band = 'within') * 1.0 / count(*) as headway_within_share
    from {{ grid }} g
    join {{ ref('fct_headways') }} h
        on h.line = g.line
        and h.status = 'valid'
        and h.known_at <= g.prediction_ts
        and h.to_arrival_ts <= g.prediction_ts
        and h.to_arrival_ts > g.prediction_ts - interval 60 minute
    group by g.line, g.prediction_ts
),

alert_times as (
    select distinct prediction_ts from {{ grid }}
),

latest_alerts as (
    select
        t.prediction_ts,
        c.fetched_at as alert_capture_at,
        c.disrupted_lines
    from alert_times t
    asof left join {{ ref('int_forecast_alert_captures') }} c on t.prediction_ts >= c.fetched_at
    where c.fetched_at is null or t.prediction_ts - c.fetched_at <= interval 15 minute
),

snapshots as (
    select
        line,
        collected_at,
        count(*) as n,
        count(*) filter (where schedule_relationship = 'SKIPPED') as n_skipped
    from {{ ref('fct_stop_predictions') }}
    where not is_duplicate_capture and schedule_matched
    group by line, capture_id, collected_at
),

skipped as (
    select
        g.line,
        g.prediction_ts,
        sum(s.n) as n_snapshots_1h,
        sum(s.n_skipped) * 1.0 / sum(s.n) as skipped_share_1h
    from {{ grid }} g
    join snapshots s
        on s.line = g.line
        and s.collected_at <= g.prediction_ts
        and s.collected_at > g.prediction_ts - interval 60 minute
    group by g.line, g.prediction_ts
)

select
    g.line,
    g.prediction_ts,
    d.rolling_median_delay_30m,
    coalesce(d.n_arrivals_30m, 0) as n_arrivals_30m,
    d.rolling_median_delay_60m,
    coalesce(d.n_arrivals_60m, 0) as n_arrivals_60m,
    h.headway_within_share,
    coalesce(h.n_headways_60m, 0) as n_headways_60m,
    case when la.alert_capture_at is null then null else list_contains(la.disrupted_lines, g.line) end as active_disruption_alert,
    s.skipped_share_1h,
    coalesce(s.n_snapshots_1h, 0) as n_snapshots_1h
from {{ grid }} g
left join delays d on d.line = g.line and d.prediction_ts = g.prediction_ts
left join headways h on h.line = g.line and h.prediction_ts = g.prediction_ts
left join latest_alerts la on la.prediction_ts = g.prediction_ts
left join skipped s on s.line = g.line and s.prediction_ts = g.prediction_ts
{% endmacro %}

{% macro forecast_example_features(targets, line_features) %}
with targets as (
    select * from {{ targets }}
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
left join {{ line_features }} f on f.line = t.line and f.prediction_ts = t.prediction_ts
asof left join snapshots m
    on m.trip_instance_key = t.trip_instance_key and m.stop_sequence = t.stop_sequence
    and t.prediction_ts >= m.collected_at
{% endmacro %}
