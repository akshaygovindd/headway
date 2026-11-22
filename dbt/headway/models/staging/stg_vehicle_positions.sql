-- One row per vehicle entity per capture, with the curated lineage columns.
select
    capture_id,
    run_id,
    content_hash,
    fetched_at,
    feed_header_ts,
    vehicle_id,
    trip_id,
    route_id,
    direction_id,
    lat,
    lon,
    bearing,
    speed,
    current_status,
    stop_id,
    current_stop_sequence,
    event_ts,
    is_stale_position
from {{ source('curated', 'vehicle_positions') }}
