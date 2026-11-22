-- One row per shape point of each current subway route, with the route colour.
-- Shapes come from the same feed version as the route definition in dim_route.
with route_shape_ids as (
    select distinct t.feed_version, t.route_id, t.shape_id
    from {{ ref('stg_gtfs__trips') }} t
    where t.shape_id is not null
)

select
    r.route_id,
    r.route_color,
    s.shape_id,
    s.shape_pt_sequence,
    s.shape_pt_lat,
    s.shape_pt_lon
from {{ ref('dim_route') }} r
join route_shape_ids i on i.route_id = r.route_id and i.feed_version = r.feed_version
join {{ ref('stg_gtfs__shapes') }} s
    on s.feed_version = i.feed_version and s.shape_id = i.shape_id
where r.is_subway
