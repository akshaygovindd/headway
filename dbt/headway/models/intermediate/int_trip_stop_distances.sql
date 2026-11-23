-- Scheduled stops of each trip instance with their distance along the trip's shape. Trips
-- without a shape, and stops without a projection, have a null dist_m and are left out of
-- arrival reconstruction.
select
    s.trip_instance_key,
    s.service_date,
    s.feed_version,
    s.trip_id,
    s.line,
    s.direction_id,
    t.shape_id,
    s.stop_sequence,
    s.stop_id,
    s.scheduled_arrival_ts,
    p.dist_m
from {{ ref('int_scheduled_stop_times') }} s
join {{ ref('stg_gtfs__trips') }} t
    on t.feed_version = s.feed_version and t.trip_id = s.trip_id
left join {{ ref('stg_gtfs__stop_shape_positions') }} p
    on p.feed_version = s.feed_version
    and p.shape_id = t.shape_id
    and p.stop_sequence = s.stop_sequence
    and p.stop_id = s.stop_id
