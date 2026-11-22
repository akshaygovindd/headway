-- Current definition of each route: taken from the latest loaded feed version.
select
    route_id,
    route_id as line,
    route_short_name,
    route_long_name,
    route_type,
    route_type in ({{ var('subway_route_types') | join(', ') }}) as is_subway,
    line_id,
    route_color,
    feed_version
from {{ ref('stg_gtfs__routes') }} r
qualify row_number() over (
    partition by route_id
    order by (select feed_start_date from {{ ref('stg_gtfs__feed_info') }} f where f.feed_version = r.feed_version) desc,
        feed_version desc
) = 1
