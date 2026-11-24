-- Line-level features of the forecast examples (Release 3, ADR-019), one row per line and
-- prediction time t. Every input is read through the time it was known, never the time of the
-- event it describes: an arrival or headway counts only when known_at <= t (the capture that
-- determines it exists), a snapshot only when collected_at <= t, an alerts capture only when
-- fetched_at <= t. The leakage tests (unit tests per feature, and the truncation check in
-- scripts/e2e.py) hold this.
--   rolling_median_delay_30m / _60m  median deviation from the timetable of the line's usable
--                         arrivals (nearest run, plausible, within an hour) with arrival_ts in
--                         the previous 30 / 60 minutes and known_at <= t
--   headway_within_share  share of the valid headways completed in the previous 60 minutes
--                         (known_at <= t) whose band is `within`; n_headways_60m is the count
--   active_disruption_alert  the latest alerts capture at or before t, if at most 15 minutes old,
--                         has a line-scope service disruption on the line (null with no such capture)
--   skipped_share_1h      share of the line's non-duplicate, schedule-matched trip update
--                         snapshots collected in the previous hour (collected_at <= t) whose stop
--                         is SKIPPED; n_snapshots_1h is the count
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
    from {{ ref('int_forecast_grid') }} g
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
    from {{ ref('int_forecast_grid') }} g
    join {{ ref('fct_headways') }} h
        on h.line = g.line
        and h.status = 'valid'
        and h.known_at <= g.prediction_ts
        and h.to_arrival_ts <= g.prediction_ts
        and h.to_arrival_ts > g.prediction_ts - interval 60 minute
    group by g.line, g.prediction_ts
),

alert_times as (
    select distinct prediction_ts from {{ ref('int_forecast_grid') }}
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
    from {{ ref('int_forecast_grid') }} g
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
from {{ ref('int_forecast_grid') }} g
left join delays d on d.line = g.line and d.prediction_ts = g.prediction_ts
left join headways h on h.line = g.line and h.prediction_ts = g.prediction_ts
left join latest_alerts la on la.prediction_ts = g.prediction_ts
left join skipped s on s.line = g.line and s.prediction_ts = g.prediction_ts
