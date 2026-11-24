-- Prediction times of the forecast examples (Release 3, ADR-019): every quarter hour (:00, :15,
-- :30, :45) of the service hours (05:00 to 01:00 local) of each line that has scheduled service on
-- the day, in hours that are not a data outage. The service day starts at the cutoff hour, so
-- hours 0 belong to the evening of the service date. An hour whose vehicle_positions window is
-- incomplete counts as an outage (int_hour_flags), so the hour still in progress has no times.
-- Dropping outage hours is a choice of training rows, not a feature: it looks at the whole hour.
with hours as (
    select
        f.service_date,
        f.hour_local,
        (
            cast(f.service_date as timestamp)
            + to_hours(f.hour_local + case when f.hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
        ) at time zone '{{ var("timezone") }}' as hour_start
    from {{ ref('int_hour_flags') }} f
    where not f.data_outage
        and (f.hour_local >= 5 or f.hour_local = 0)
),

lines as (
    select distinct service_date, line from {{ ref('int_scheduled_stop_times') }}
),

quarters as (
    select * from (values (0), (15), (30), (45)) as q (minute_of_hour)
)

select
    l.line,
    l.service_date,
    h.hour_local,
    h.hour_start + to_minutes(q.minute_of_hour) as prediction_ts,
    case when dayofweek(l.service_date) in (0, 6) then 'weekend' else 'weekday' end as day_type
from hours h
join lines l on l.service_date = h.service_date
cross join quarters q
