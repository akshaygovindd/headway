{{ config(tags=['r2'], severity='error') }}
-- The capture-completeness objective counts the same minutes as the manifests do. Per service
-- date, good_events of capture_completeness in agg_slo_daily (minutes in which poll 0 of all three
-- feeds was captured ok) against the minutes in which the curated manifests hold an ok poll-0
-- entry of all three feeds, read from the entries without the staging dedupe. Tolerance: one
-- minute, or 0.1% of the day's minutes. Closed days come from history, so a day written before a
-- fix fails here until it is rebuilt (DEPLOY.md, rebuild_history).
with manifest_minutes as (
    select date_trunc('minute', scheduled_at) as minute
    from {{ ref('stg_manifest_entries') }}
    where poll_index = 0 and status = 'ok'
    group by 1
    having count(distinct feed) = 3
),

manifest_days as (
    select {{ service_date_from_ts('minute') }} as service_date, count(*) as good_minutes
    from manifest_minutes
    group by 1
)

select
    s.service_date,
    s.total_events as minutes,
    s.good_events as slo_good_minutes,
    coalesce(m.good_minutes, 0) as manifest_good_minutes
from {{ ref('agg_slo_daily') }} s
left join manifest_days m on m.service_date = s.service_date
where s.slo_name = 'capture_completeness'
    and abs(s.good_events - coalesce(m.good_minutes, 0)) > greatest(1, 0.001 * s.total_events)
