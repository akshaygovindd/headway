-- Static shape points with cumulative distance along the shape, all loaded feed versions.
select * exclude ("table")
from {{ source('curated', 'gtfs_shape_points_dist') }}
