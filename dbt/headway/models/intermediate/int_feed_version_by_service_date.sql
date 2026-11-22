-- Current GTFS feed version for each service date seen in the data: the loaded
-- version with the latest feed_start_date on or before the service date.
with service_dates as (
    select distinct service_date from {{ ref('int_captures') }} where service_date is not null
    union
    select distinct service_date from {{ ref('stg_trip_updates') }}
),

ranked as (
    select
        d.service_date,
        f.feed_version,
        f.feed_start_date,
        row_number() over (
            partition by d.service_date order by f.feed_start_date desc, f.feed_version desc
        ) as rn
    from service_dates d
    join {{ ref('stg_gtfs__feed_info') }} f on f.feed_start_date <= d.service_date
)

select service_date, feed_version, feed_start_date
from ranked
where rn = 1
