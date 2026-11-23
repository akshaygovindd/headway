-- One row per trip instance and scheduled stop that the vehicle's observed path covers: when the
-- vehicle arrived, how that was worked out, and how far to trust it.
--
-- observed:   the vehicle was seen STOPPED_AT the stop. The arrival is somewhere between the last
--             capture before the first STOPPED_AT and that STOPPED_AT; arrival_ts is the midpoint
--             and interval_width_s the width of that interval. high when the interval is at most
--             30 s, medium up to 120 s, else low. A STOPPED_AT with no earlier capture gives no
--             interval and the stop is 'missing'.
-- bracketed:  never seen STOPPED_AT; arrival_ts is interpolated by distance along the shape
--             between the last observation before the stop and the first at or after it.
--             medium when those two are at most 120 s apart (bracket_gap_s), else low.
-- missing:    the stop lies on the observed stretch but neither method applies.
--
-- monotonic_ok: arrival times must not decrease along stop_sequence. A row that disagrees with
-- the previous or next reconstructed stop of its trip is flagged false, both are set to low
-- confidence, and the violations are counted in agg_arrival_reconstruction_daily.
--
-- Observations that cannot be placed on a shape (ADDED trips, unscheduled trips, no shape) are
-- not reconstructed; int_vehicle_stop_events keeps them with an exclusion_reason so they are
-- counted. A trip is reconstructed from the single vehicle with most observations on it.
with usable as (
    select *
    from {{ ref('int_vehicle_stop_events') }}
    where exclusion_reason is null
),

chosen as (
    select trip_instance_key, vehicle_id
    from usable
    group by trip_instance_key, vehicle_id
    qualify row_number() over (
        partition by trip_instance_key order by count(*) desc, vehicle_id
    ) = 1
),

ev as (
    select
        u.trip_instance_key,
        u.vehicle_id,
        row_number() over (
            partition by u.trip_instance_key order by u.event_ts, u.capture_id
        ) as n,
        u.event_ts,
        u.dist_m,
        u.current_status,
        u.current_stop_sequence
    from usable u
    join chosen c on c.trip_instance_key = u.trip_instance_key and c.vehicle_id = u.vehicle_id
),

span as (
    select trip_instance_key, min(dist_m) as min_dist_m, max(dist_m) as max_dist_m
    from ev
    group by trip_instance_key
),

-- stops on the observed stretch of each observed trip
stops as (
    select s.*
    from {{ ref('int_trip_stop_distances') }} s
    join span r
        on r.trip_instance_key = s.trip_instance_key
        and s.dist_m between r.min_dist_m and r.max_dist_m
),

-- for each stop: the first STOPPED_AT there, and the observations either side of it
located as (
    select
        s.trip_instance_key,
        s.stop_sequence,
        min(e.n) filter (
            where e.current_status = 'STOPPED_AT' and e.current_stop_sequence = s.stop_sequence
        ) as n_stopped,
        max(e.n) filter (where e.dist_m < s.dist_m) as n_before,
        min(e.n) filter (where e.dist_m >= s.dist_m) as n_after
    from stops s
    join ev e on e.trip_instance_key = s.trip_instance_key
    group by s.trip_instance_key, s.stop_sequence
),

estimated as (
    select
        s.trip_instance_key,
        s.stop_sequence,
        s.service_date,
        s.trip_id,
        s.line,
        s.direction_id,
        s.stop_id,
        s.dist_m,
        s.scheduled_arrival_ts,
        v.vehicle_id,
        case
            when l.n_stopped > 1 then 'observed'
            when l.n_stopped is null and l.n_before is not null and l.n_after is not null then 'bracketed'
            else 'missing'
        end as method,
        -- observed interval: previous capture to first STOPPED_AT
        pv.event_ts as interval_start_ts,
        st.event_ts as interval_end_ts,
        bf.event_ts as before_ts,
        bf.dist_m as before_dist_m,
        af.event_ts as after_ts,
        af.dist_m as after_dist_m
    from stops s
    join located l on l.trip_instance_key = s.trip_instance_key and l.stop_sequence = s.stop_sequence
    join (select distinct trip_instance_key, vehicle_id from ev) v on v.trip_instance_key = s.trip_instance_key
    left join ev st on st.trip_instance_key = s.trip_instance_key and st.n = l.n_stopped
    left join ev pv on pv.trip_instance_key = s.trip_instance_key and pv.n = l.n_stopped - 1
    left join ev bf on bf.trip_instance_key = s.trip_instance_key and bf.n = l.n_before
    left join ev af on af.trip_instance_key = s.trip_instance_key and af.n = l.n_after
),

timed as (
    select
        *,
        case method
            when 'observed' then interval_start_ts + to_microseconds(cast(round(
                1e6 * (epoch(interval_end_ts) - epoch(interval_start_ts)) / 2
            ) as bigint))
            when 'bracketed' then before_ts + to_microseconds(cast(round(
                1e6 * (epoch(after_ts) - epoch(before_ts)) * (dist_m - before_dist_m)
                    / (after_dist_m - before_dist_m)
            ) as bigint))
        end as arrival_ts,
        case when method = 'observed' then epoch(interval_end_ts) - epoch(interval_start_ts) end as interval_width_s,
        case when method = 'bracketed' then epoch(after_ts) - epoch(before_ts) end as bracket_gap_s
    from estimated
),

-- compare each reconstructed stop with the neighbouring reconstructed stops of its trip
checked as (
    select
        *,
        arrival_ts < lag(arrival_ts ignore nulls) over w
            or arrival_ts > lead(arrival_ts ignore nulls) over w as is_out_of_order
    from timed
    window w as (partition by trip_instance_key order by stop_sequence)
)

select
    trip_instance_key,
    stop_sequence,
    service_date,
    trip_id,
    line,
    direction_id,
    stop_id,
    vehicle_id,
    arrival_ts,
    method,
    case
        when method = 'missing' then null
        when coalesce(is_out_of_order, false) then 'low'
        when method = 'observed' and interval_width_s <= 30 then 'high'
        when method = 'observed' and interval_width_s <= 120 then 'medium'
        when method = 'bracketed' and bracket_gap_s <= 120 then 'medium'
        else 'low'
    end as confidence,
    interval_width_s,
    bracket_gap_s,
    not coalesce(is_out_of_order, false) as monotonic_ok,
    dist_m,
    scheduled_arrival_ts
from checked
