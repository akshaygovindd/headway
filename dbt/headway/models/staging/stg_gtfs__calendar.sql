-- Static GTFS calendar, all loaded feed versions (select the current one per service
-- date through int_feed_version_by_service_date).
select * exclude ("table")
from {{ source('curated', 'gtfs_calendar') }}
