-- service_ids running on each service date: calendar weekday pattern within its
-- date range, plus calendar_dates additions, minus removals.
with base as (
    select v.service_date, v.feed_version, c.service_id
    from {{ ref('int_feed_version_by_service_date') }} v
    join {{ ref('stg_gtfs__calendar') }} c
        on c.feed_version = v.feed_version
        and v.service_date between c.start_date and c.end_date
        and case dayofweek(v.service_date)
            when 0 then c.sunday
            when 1 then c.monday
            when 2 then c.tuesday
            when 3 then c.wednesday
            when 4 then c.thursday
            when 5 then c.friday
            else c.saturday
        end
),

added as (
    select v.service_date, v.feed_version, e.service_id
    from {{ ref('int_feed_version_by_service_date') }} v
    join {{ ref('stg_gtfs__calendar_dates') }} e
        on e.feed_version = v.feed_version and e.date = v.service_date and e.exception_type = 1
),

removed as (
    select v.service_date, v.feed_version, e.service_id
    from {{ ref('int_feed_version_by_service_date') }} v
    join {{ ref('stg_gtfs__calendar_dates') }} e
        on e.feed_version = v.feed_version and e.date = v.service_date and e.exception_type = 2
),

active as (
    select * from base
    union
    select * from added
)

select a.service_date, a.feed_version, a.service_id
from active a
left join removed r
    on r.service_date = a.service_date and r.feed_version = a.feed_version and r.service_id = a.service_id
where r.service_id is null
