-- Current definition of each stop: taken from the latest loaded feed version.
select
    stop_id,
    stop_name,
    stop_lat,
    stop_lon,
    location_type,
    parent_station,
    feed_version
from {{ ref('stg_gtfs__stops') }} s
qualify row_number() over (
    partition by stop_id
    order by (select feed_start_date from {{ ref('stg_gtfs__feed_info') }} f where f.feed_version = s.feed_version) desc,
        feed_version desc
) = 1
