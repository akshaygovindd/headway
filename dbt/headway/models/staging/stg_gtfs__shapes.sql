-- Static GTFS shape points, all loaded feed versions.
select * exclude ("table")
from {{ source('curated', 'gtfs_shapes') }}
