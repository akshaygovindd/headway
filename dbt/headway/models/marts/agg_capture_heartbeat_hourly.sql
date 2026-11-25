-- Capture heartbeat per local service hour (Release 4): how many one-minute runs were expected and
-- missed, the longest stretch without fresh data, and whether the hour is a data outage (see
-- int_hour_flags). Built from the hour flags, the feed_gap_min check of agg_quality_checks_hourly
-- and the observed runs; expected_runs is observed plus missed, which is the clipped minute grid of
-- agg_quality_checks_hourly. The finer per-run table (agg_pipeline_health) is not needed: the
-- public heartbeat is hourly.
with observed as (
    select service_date, hour_local, count(distinct run_id) as observed_runs
    from {{ ref('int_captures') }}
    group by 1, 2
),

gaps as (
    select service_date, hour_local, value as feed_gap_min
    from {{ ref('agg_quality_checks_hourly') }}
    where check_name = 'feed_gap_min'
)

select
    f.service_date,
    f.hour_local,
    (
        cast(f.service_date as timestamp)
        + to_hours(f.hour_local + case when f.hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
    ) at time zone '{{ var("timezone") }}' as hour_start,
    coalesce(o.observed_runs, 0) + f.missed_runs as expected_runs,
    f.missed_runs,
    coalesce(g.feed_gap_min, 0) as feed_gap_min,
    f.data_outage
from {{ ref('int_hour_flags') }} f
left join observed o on o.service_date = f.service_date and o.hour_local = f.hour_local
left join gaps g on g.service_date = f.service_date and g.hour_local = f.hour_local
