-- One row per vehicle position observation of a trip: de-duplicated across captures, stale
-- positions removed, and placed on the trip's shape as metres from its start.
--
-- trip_instance_key is trip_id | service date of event_ts | scheduled start time, the same key the
-- trip updates use; vehicle positions carry no start date, so it is derived from the schedule.
-- Observations that cannot be placed (ADDED trips, trips not in the schedule for that day, trips
-- without a shape) are kept with exclusion_reason set so they are counted, and are not used for
-- arrival reconstruction.
--
-- dist_m: a STOPPED_AT observation is pinned to its stop's position on the shape; any other
-- observation takes the distance of the nearest shape point within about 500 m (no spatial
-- extension). dist_m is then made non-decreasing along the trip (running maximum) because a
-- vehicle does not run backwards; raw_dist_m keeps the unsmoothed value.
--
-- segment: a trip id can be reused within a service date (the same vehicle runs it again). When a
-- vehicle's current_stop_sequence on a trip id drops below its previous observation the vehicle
-- has started a new run, so segment is incremented (0 for the first run). dist_m is made
-- non-decreasing within a segment only, so a restart is never a backward jump.
with observations as (
    select
        vehicle_id,
        trip_id,
        route_id,
        direction_id,
        lat,
        lon,
        current_status,
        stop_id,
        current_stop_sequence,
        event_ts,
        capture_id,
        fetched_at,
        {{ service_date_from_ts('event_ts') }} as service_date
    from {{ ref('stg_vehicle_positions') }}
    where event_ts is not null and not is_stale_position and trip_id is not null
        and {{ compute_days_filter(service_date_from_ts('event_ts')) }}
    -- the same position is repeated by every capture until the vehicle reports again
    qualify row_number() over (
        partition by vehicle_id, trip_id, event_ts order by fetched_at, capture_id
    ) = 1
),

trips as (
    select distinct
        trip_id, service_date, trip_instance_key, feed_version, shape_id
    from {{ ref('int_trip_stop_distances') }}
),

keyed as (
    select
        o.*,
        coalesce(t.trip_instance_key, o.trip_id || '|' || o.service_date::varchar || '|') as trip_instance_key,
        t.feed_version,
        t.shape_id,
        case
            when o.trip_id like 'ADDED-%' then 'added_trip'
            when t.trip_instance_key is null then 'unscheduled_trip'
            when t.shape_id is null then 'no_shape'
        end as exclusion_reason
    from observations o
    left join trips t on t.trip_id = o.trip_id and t.service_date = o.service_date
),

-- a STOPPED_AT observation sits at its stop: use the stop's position on the shape
stop_positions as (
    select feed_version, shape_id, stop_sequence, min(dist_m) as dist_m
    from {{ ref('stg_gtfs__stop_shape_positions') }}
    group by feed_version, shape_id, stop_sequence
),

pinned as (
    select k.*, sp.dist_m as pinned_dist_m
    from keyed k
    left join stop_positions sp
        on k.current_status = 'STOPPED_AT'
        and sp.feed_version = k.feed_version
        and sp.shape_id = k.shape_id
        and sp.stop_sequence = k.current_stop_sequence
),

-- nearest shape point within the box, for the other observations
candidates as (
    select
        k.vehicle_id,
        k.trip_instance_key,
        k.event_ts,
        p.dist_m,
        (p.lat - k.lat) * (p.lat - k.lat)
            + ((p.lon - k.lon) * cos(radians(k.lat))) * ((p.lon - k.lon) * cos(radians(k.lat))) as d2
    from pinned k
    join {{ ref('stg_gtfs__shape_points_dist') }} p
        on p.feed_version = k.feed_version
        and p.shape_id = k.shape_id
        and p.lat between k.lat - 0.0045 and k.lat + 0.0045
        and p.lon between k.lon - 0.006 and k.lon + 0.006
    where k.exclusion_reason is null and k.pinned_dist_m is null
),

nearest as (
    select vehicle_id, trip_instance_key, event_ts, dist_m
    from candidates
    qualify row_number() over (
        partition by vehicle_id, trip_instance_key, event_ts order by d2, dist_m
    ) = 1
),

placed as (
    select
        k.*,
        coalesce(k.pinned_dist_m, n.dist_m) as raw_dist_m
    from pinned k
    left join nearest n
        on n.vehicle_id = k.vehicle_id
        and n.trip_instance_key = k.trip_instance_key
        and n.event_ts = k.event_ts
),

-- 1 where the stop sequence went backwards from the vehicle's previous observation of the trip id
resets as (
    select
        *,
        case when current_stop_sequence < lag(current_stop_sequence) over w then 1 else 0 end as is_reset
    from placed
    window w as (partition by vehicle_id, trip_instance_key order by event_ts, capture_id)
),

segmented as (
    select
        *,
        sum(is_reset) over (
            partition by vehicle_id, trip_instance_key order by event_ts, capture_id
            rows between unbounded preceding and current row
        ) as segment
    from resets
),

computed as (
select
    vehicle_id,
    trip_instance_key,
    segment,
    event_ts,
    capture_id,
    fetched_at,
    service_date,
    trip_id,
    route_id as line,
    direction_id,
    shape_id,
    current_status,
    current_stop_sequence,
    stop_id,
    lat,
    lon,
    raw_dist_m,
    max(raw_dist_m) over (
        partition by vehicle_id, trip_instance_key, segment order by event_ts, capture_id
        rows between unbounded preceding and current row
    ) as dist_m,
    coalesce(exclusion_reason, case when raw_dist_m is null then 'off_shape' end) as exclusion_reason
from segmented
)

{{ fact_union('int_vehicle_stop_events') }}
