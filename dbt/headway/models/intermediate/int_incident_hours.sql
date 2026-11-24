-- One row per line, direction, service date and hour, with the incident rules evaluated (Release 3,
-- docs/METRICS.md "Incidents"). An hour breaches when any rule is crossed:
--   deviation  median actual deviation >= baseline + incidents.deviation_over_baseline_s, where the
--              baseline is the median of the same line, direction, day type (weekend = Saturday and
--              Sunday) and hour over EARLIER service days. The rule is off (baseline_enabled false)
--              until those days number incidents.min_baseline_days.
--   gap        headway gap share >= incidents.gap_share_pct
--   skipped    skipped-stop share >= incidents.skipped_share_pct
--   alert      a line-scope service-disruption alert is active
-- Data-outage hours never breach (data_outage = true; they are recorded, not judged).
-- hour_start is the local clock time of the start of the hour on the service day timeline.
{% set cfg = var('incidents') %}
with dev as (
    select
        line, direction_id, service_date, hour_local, data_outage, median_deviation_s, coverage_pct,
        case when dayofweek(service_date) in (0, 6) then 'weekend' else 'weekday' end as day_type,
        cast(service_date as timestamp)
            + to_hours(hour_local + case when hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
            as hour_start
    from {{ ref('int_deviation_hour_history') }}
),

baseline as (
    select
        a.line, a.direction_id, a.service_date, a.hour_local,
        count(distinct b.service_date) as baseline_days,
        median(b.median_deviation_s) as baseline_median_s
    from dev a
    join dev b
        on b.line = a.line
        and b.direction_id is not distinct from a.direction_id
        and b.day_type = a.day_type
        and b.hour_local = a.hour_local
        and b.service_date < a.service_date
    where not b.data_outage and b.median_deviation_s is not null
    group by 1, 2, 3, 4
),

joined as (
    select
        d.line, d.direction_id, d.service_date, d.hour_local, d.hour_start, d.day_type, d.data_outage,
        d.median_deviation_s, d.coverage_pct,
        coalesce(b.baseline_days, 0) as baseline_days,
        b.baseline_median_s,
        coalesce(b.baseline_days, 0) >= {{ cfg['min_baseline_days'] }} as baseline_enabled,
        h.pct_gap,
        r.skipped_share * 100.0 as skipped_pct,
        al.line is not null as alert_active
    from dev d
    left join baseline b
        on b.line = d.line and b.direction_id is not distinct from d.direction_id
        and b.service_date = d.service_date and b.hour_local = d.hour_local
    left join {{ ref('agg_headway_adherence_hourly') }} h
        on h.line = d.line and h.direction_id is not distinct from d.direction_id
        and h.service_date = d.service_date and h.hour_local = d.hour_local
    left join {{ ref('agg_line_reliability_hourly') }} r
        on r.line = d.line and r.direction_id is not distinct from d.direction_id
        and r.service_date = d.service_date and r.hour_local = d.hour_local
    left join {{ ref('int_disrupted_line_hours') }} al
        on al.line = d.line and al.service_date = d.service_date and al.hour_local = d.hour_local
),

flagged as (
    select
        *,
        not data_outage and baseline_enabled and median_deviation_s is not null
            and median_deviation_s - baseline_median_s >= {{ cfg['deviation_over_baseline_s'] }} as crossed_deviation,
        not data_outage and coalesce(pct_gap >= {{ cfg['gap_share_pct'] }}, false) as crossed_gap,
        not data_outage and coalesce(skipped_pct >= {{ cfg['skipped_share_pct'] }}, false) as crossed_skipped,
        not data_outage and alert_active as crossed_alert
    from joined
)

select
    *,
    crossed_deviation or crossed_gap or crossed_skipped or crossed_alert as breach
from flagged
