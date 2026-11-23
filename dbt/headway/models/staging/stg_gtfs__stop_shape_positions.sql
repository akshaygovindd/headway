-- Each stop of a trip pattern projected onto its shape, all loaded feed versions.
select * exclude ("table")
from {{ source('curated', 'gtfs_stop_shape_positions') }}
