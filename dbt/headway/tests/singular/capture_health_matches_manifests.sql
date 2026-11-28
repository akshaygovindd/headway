{{ config(severity='error') }}
-- A minute's capture health agrees with the manifests' own feed statuses. Per service date, the
-- minutes in which agg_pipeline_health reports trip_updates, vehicle_positions and alerts all ok
-- (the status of each feed's poll-0 fetch) are counted against the minutes in which the curated
-- manifests hold an ok poll-0 entry of all three feeds, read straight from the entries without
-- the dedupe and poll collapsing of stg_manifest_deliveries. The two must agree within a small
-- tolerance (one minute, or 0.1% of the day's minutes). A feed status that goes missing, as the
-- vehicle status did once a minute held six entries and poll 0 was overwritten by poll 1, fails
-- here instead of reading as lost capture (DATA_QUALITY.md).
with health as (
    select
        date_trunc('minute', scheduled_at) as minute,
        bool_or(trip_updates_status = 'ok' and vehicle_positions_status = 'ok' and alerts_status = 'ok') as all_ok
    from {{ ref('agg_pipeline_health') }}
    group by 1
),

health_days as (
    select
        {{ service_date_from_ts('minute') }} as service_date,
        count(*) as minutes,
        count(*) filter (where all_ok) as good_minutes
    from health
    group by 1
),

manifest_minutes as (
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
    h.service_date,
    h.minutes,
    h.good_minutes as pipeline_health_good_minutes,
    coalesce(m.good_minutes, 0) as manifest_good_minutes
from health_days h
left join manifest_days m on m.service_date = h.service_date
where abs(h.good_minutes - coalesce(m.good_minutes, 0)) > greatest(1, 0.001 * h.minutes)
