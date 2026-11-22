-- Every trip instance seen in the schedule or in realtime. trip_instance_key is
-- trip_id + service_date + start_time; service_date comes from the trip's
-- start_date, else from the capture time.
with scheduled as (
    select distinct
        trip_instance_key, trip_id, service_date, start_time, route_id, direction_id, trip_headsign
    from {{ ref('int_scheduled_stop_times') }}
),

realtime as (
    select
        trip_instance_key,
        any_value(trip_id) as trip_id,
        any_value(service_date) as service_date,
        any_value(start_time) as start_time,
        arg_max(route_id, fetched_at) as route_id,
        arg_max(direction_id, fetched_at) as direction_id,
        bool_or(schedule_relationship = 'ADDED' or trip_id like 'ADDED-%') as is_added_trip
    from {{ ref('stg_trip_updates') }}
    group by trip_instance_key
)

select
    coalesce(s.trip_instance_key, r.trip_instance_key) as trip_instance_key,
    coalesce(s.trip_id, r.trip_id) as trip_id,
    coalesce(s.service_date, r.service_date) as service_date,
    coalesce(s.start_time, r.start_time) as start_time,
    coalesce(s.route_id, r.route_id) as route_id,
    coalesce(s.route_id, r.route_id) as line,
    coalesce(s.direction_id, r.direction_id) as direction_id,
    s.trip_headsign,
    s.trip_instance_key is not null as is_scheduled,
    r.trip_instance_key is not null as is_observed_realtime,
    coalesce(r.is_added_trip, false) as is_added_trip
from scheduled s
full outer join realtime r on r.trip_instance_key = s.trip_instance_key
