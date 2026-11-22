-- Scheduled stop times for subway routes on each active service date.
-- start_time is the first stop's departure time, matching the GTFS-realtime
-- trip start_time of scheduled trips.
with trip_start as (
    select
        feed_version,
        trip_id,
        arg_min(coalesce(departure_time, arrival_time), stop_sequence) as start_time
    from {{ ref('stg_gtfs__stop_times') }}
    group by feed_version, trip_id
),

scheduled as (
    select
        s.service_date,
        t.feed_version,
        t.trip_id,
        t.route_id,
        t.direction_id,
        t.trip_headsign,
        ts.start_time,
        st.stop_id,
        st.stop_sequence,
        st.arrival_time,
        st.departure_time
    from {{ ref('int_active_services') }} s
    join {{ ref('stg_gtfs__trips') }} t
        on t.feed_version = s.feed_version and t.service_id = s.service_id
    join {{ ref('stg_gtfs__routes') }} r
        on r.feed_version = t.feed_version and r.route_id = t.route_id
        and r.route_type in ({{ var('subway_route_types') | join(', ') }})
    join trip_start ts on ts.feed_version = t.feed_version and ts.trip_id = t.trip_id
    join {{ ref('stg_gtfs__stop_times') }} st
        on st.feed_version = t.feed_version and st.trip_id = t.trip_id
)

select
    trip_id || '|' || service_date::varchar || '|' || coalesce(start_time, '') as trip_instance_key,
    service_date,
    feed_version,
    trip_id,
    route_id,
    route_id as line,
    direction_id,
    trip_headsign,
    start_time,
    stop_id,
    stop_sequence,
    arrival_time,
    departure_time,
    {{ gtfs_time_to_utc('service_date', 'coalesce(arrival_time, departure_time)') }} as scheduled_arrival_ts,
    cast(extract(
        hour from {{ gtfs_time_to_utc('service_date', 'coalesce(arrival_time, departure_time)') }}
            at time zone '{{ var("timezone") }}'
    ) as integer) as hour_local
from scheduled
