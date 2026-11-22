-- One row per vehicle position event. When several captures repeat the same
-- vehicle_id and event_ts, the first capture wins.
with ranked as (
    select
        *,
        row_number() over (
            partition by vehicle_id, event_ts order by fetched_at, capture_id
        ) as rn,
        count(*) over (partition by vehicle_id, event_ts) as n_captures_seen
    from {{ ref('stg_vehicle_positions') }}
    where event_ts is not null
)

select
    vehicle_id,
    event_ts,
    capture_id,
    run_id,
    fetched_at as collected_at,
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
    is_stale_position,
    n_captures_seen
from ranked
where rn = 1
