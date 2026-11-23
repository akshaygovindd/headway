-- Service hours inside each rolling window, for the exact window marts. The windows end at the
-- start of the latest hour in agg_line_reliability_hourly (the export's metrics_as_of): last_24h
-- keeps hours starting within 24 hours of it, last_7d within 168. One row per window and hour.
with hours as (
    select
        service_date,
        hour_local,
        (
            cast(service_date as timestamp)
            + to_hours(hour_local + case when hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
        ) at time zone '{{ var("timezone") }}' as hour_start
    from {{ ref('agg_line_reliability_hourly') }}
    group by service_date, hour_local
),

windows as (
    select * from (values ('last_24h', 24), ('last_7d', 168)) as w (window_label, n_hours)
)

select w.window_label, h.service_date, h.hour_local, h.hour_start
from hours h
cross join windows w
where h.hour_start > (select max(hour_start) from hours) - to_hours(w.n_hours)
