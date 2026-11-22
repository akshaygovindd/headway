-- One row per loaded GTFS feed version with its validity window.
select * exclude ("table")
from {{ source('curated', 'gtfs_feed_info') }}
