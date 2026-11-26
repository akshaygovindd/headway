-- Observed slow segments: segments that run slower than their own normal (Release 4, Phase 4.5;
-- METRICS.md "Slow segments"). An observed speed-restriction signal, not the MBTA's list.
--   normal_s    median travel time of the segment and time band over the last 7 days
--   observed_s  median over the window's trips: last_24h is the last 24 hours; last_7d is the latest
--               3 service days of the 7 (a recent change shows against the week)
-- flagged: n_trips >= min_trips, observed at least min_excess_pct and min_excess_s above normal, and
-- at least min_share_pct of the trips individually min_excess_s or more above normal. Severity is
-- the excess per trip. first_seen/last_seen are the first and last slow trip of the window.
{% set cfg = var('slow_segments', {}) %}
{% set min_trips = cfg.get('min_trips', 15) %}
{% set min_excess_pct = cfg.get('min_excess_pct', 20) %}
{% set min_excess_s = cfg.get('min_excess_s', 30) %}
{% set min_share_pct = cfg.get('min_share_pct', 60) %}
{% set moderate_s = cfg.get('moderate_s', 60) %}
{% set severe_s = cfg.get('severe_s', 120) %}
with recent_days as (
    select distinct service_date
    from {{ ref('int_window_hours') }}
    where window_label = 'last_7d'
    qualify dense_rank() over (order by service_date desc) <= 3
),

trips as (
    select
        t.*,
        w7.hour_start is not null as in_7d,
        w24.hour_start is not null as in_24h,
        r.service_date is not null as in_recent_days,
        t.travel_s between 10 and 3600 as plausible
    from {{ ref('int_segment_travel_times') }} t
    left join {{ ref('int_window_hours') }} w7
        on w7.window_label = 'last_7d' and w7.service_date = t.service_date and w7.hour_local = t.hour_local
    left join {{ ref('int_window_hours') }} w24
        on w24.window_label = 'last_24h' and w24.service_date = t.service_date and w24.hour_local = t.hour_local
    left join recent_days r on r.service_date = t.service_date and w7.hour_start is not null
    where w7.hour_start is not null and not t.data_outage
),

normal as (
    select
        line, direction_id, from_stop_id, to_stop_id, time_band,
        count(*) filter (where plausible) as n_baseline_trips,
        median(travel_s) filter (where plausible) as normal_s
    from trips
    group by 1, 2, 3, 4, 5
),

windowed as (
    select 'last_24h' as window_label, * from trips where in_24h
    union all
    select 'last_7d' as window_label, * from trips where in_recent_days
),

stats as (
    select
        w.line, w.direction_id, w.from_stop_id, w.to_stop_id, w.time_band, w.window_label,
        count(*) as n_scheduled_trips,
        count(*) filter (where w.plausible) as n_trips,
        count(*) filter (where w.plausible and w.service_disruption) as n_disruption,
        any_value(n.n_baseline_trips) as n_baseline_trips,
        any_value(n.normal_s) as normal_s,
        median(w.travel_s) filter (where w.plausible) as observed_s,
        count(*) filter (where w.plausible and w.travel_s >= n.normal_s + {{ min_excess_s }}) as n_slow_trips,
        min(w.from_ts) filter (where w.plausible and w.travel_s >= n.normal_s + {{ min_excess_s }}) as first_seen,
        max(w.from_ts) filter (where w.plausible and w.travel_s >= n.normal_s + {{ min_excess_s }}) as last_seen
    from windowed w
    join normal n using (line, direction_id, from_stop_id, to_stop_id, time_band)
    group by 1, 2, 3, 4, 5, 6
),

scored as (
    select
        *,
        observed_s - normal_s as excess_s,
        100.0 * (observed_s - normal_s) / nullif(normal_s, 0) as excess_pct,
        100.0 * n_slow_trips / nullif(n_trips, 0) as share_slow_pct,
        100.0 * n_trips / nullif(n_scheduled_trips, 0) as coverage_pct
    from stats
)

select
    line, direction_id, from_stop_id, to_stop_id, time_band, window_label,
    n_scheduled_trips,
    n_trips,
    n_disruption,
    n_baseline_trips,
    round(normal_s, 1) as normal_s,
    round(observed_s, 1) as observed_s,
    round(excess_s, 1) as excess_s,
    round(excess_pct, 1) as excess_pct,
    round(share_slow_pct, 1) as share_slow_pct,
    coalesce(
        n_trips >= {{ min_trips }} and n_baseline_trips >= {{ min_trips }}
        and excess_pct >= {{ min_excess_pct }} and excess_s >= {{ min_excess_s }}
        and share_slow_pct >= {{ min_share_pct }},
        false
    ) as flagged,
    case
        when not coalesce(
            n_trips >= {{ min_trips }} and n_baseline_trips >= {{ min_trips }}
            and excess_pct >= {{ min_excess_pct }} and excess_s >= {{ min_excess_s }}
            and share_slow_pct >= {{ min_share_pct }},
            false
        ) then null
        when excess_s >= {{ severe_s }} then 'severe'
        when excess_s >= {{ moderate_s }} then 'moderate'
        else 'minor'
    end as severity,
    first_seen,
    last_seen,
    n_trips as coverage_numerator,
    n_scheduled_trips as coverage_denominator,
    round(coverage_pct, 1) as coverage_pct
from scored
