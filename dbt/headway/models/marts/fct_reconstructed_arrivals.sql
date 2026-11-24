-- One row per trip instance, run (segment) and scheduled stop that the vehicle's observed path
-- covers: when the vehicle arrived, how that was worked out, and how far to trust it.
--
-- observed:   the vehicle was seen STOPPED_AT the stop. The arrival is somewhere between the last
--             capture before the first STOPPED_AT and that STOPPED_AT; arrival_ts is the midpoint
--             and interval_width_s the width of that interval. high when the interval is at most
--             30 s, medium up to 120 s, else low. A STOPPED_AT with no earlier capture gives no
--             interval and the stop is 'missing'.
-- bracketed:  never seen STOPPED_AT; arrival_ts is interpolated by distance along the shape
--             between the nearest anchors either side of the stop. An anchor is an observed stop's
--             arrival (its midpoint) or a raw observation. At equal distance the observed
--             midpoint is the anchor after a stop (the arrival there) and the last raw observation
--             the anchor before it (the vehicle was still at the stop then). Anchor times are made non-decreasing in distance (running maximum), and the
--             stop's distance is made non-decreasing in stop_sequence, so a bracketed arrival can
--             never be earlier than a stop before it. bracket_gap_s is the time between the two
--             anchors; medium when it is at most 120 s, else low.
-- missing:    the stop lies on the observed stretch but neither method applies.
--
-- segment: a reused trip id gives several runs (see int_vehicle_stop_events); each run is
-- reconstructed on its own and there is no cross-run comparison.
--
-- known_at: the fetched_at of the last capture that the arrival, its confidence and its order flag
-- rest on (the capture of the first STOPPED_AT for an observed stop, the later of the two anchors
-- for a bracketed one, and the neighbouring reconstructed stops). A forecast made at t may use the
-- arrival only when known_at <= t, not when arrival_ts <= t (ADR-019).
--
-- monotonic_ok: arrival times must not decrease along stop_sequence. Bracketed stops satisfy this
-- by construction; a row that still disagrees with the previous or next reconstructed stop of its
-- run (two observed midpoints out of order) is flagged false, both are set to low confidence, and
-- the violations are counted in agg_arrival_reconstruction_daily.
--
-- Observations that cannot be placed on a shape (ADDED trips, unscheduled trips, no shape) are
-- not reconstructed; int_vehicle_stop_events keeps them with an exclusion_reason so they are
-- counted. A run is reconstructed from the single vehicle with most observations on it.
with usable as (
    select *
    from {{ ref('int_vehicle_stop_events') }}
    where exclusion_reason is null
),

chosen as (
    select trip_instance_key, segment, vehicle_id
    from usable
    group by trip_instance_key, segment, vehicle_id
    qualify row_number() over (
        partition by trip_instance_key, segment order by count(*) desc, vehicle_id
    ) = 1
),

ev as (
    select
        u.trip_instance_key,
        u.segment,
        u.vehicle_id,
        row_number() over (
            partition by u.trip_instance_key, u.segment order by u.event_ts, u.capture_id
        ) as n,
        u.event_ts,
        u.fetched_at,
        u.dist_m,
        u.current_status,
        u.current_stop_sequence
    from usable u
    join chosen c
        on c.trip_instance_key = u.trip_instance_key
        and c.segment = u.segment
        and c.vehicle_id = u.vehicle_id
),

span as (
    select trip_instance_key, segment, min(dist_m) as min_dist_m, max(dist_m) as max_dist_m
    from ev
    group by trip_instance_key, segment
),

-- stops on the observed stretch of each run; eff_dist_m never decreases along stop_sequence
stops as (
    select
        s.*,
        r.segment,
        max(s.dist_m) over (
            partition by s.trip_instance_key, r.segment order by s.stop_sequence
            rows between unbounded preceding and current row
        ) as eff_dist_m
    from {{ ref('int_trip_stop_distances') }} s
    join span r
        on r.trip_instance_key = s.trip_instance_key
        and s.dist_m between r.min_dist_m and r.max_dist_m
),

-- for each stop: the first STOPPED_AT there, and the observations either side of it
located as (
    select
        s.trip_instance_key,
        s.segment,
        s.stop_sequence,
        min(e.n) filter (
            where e.current_status = 'STOPPED_AT' and e.current_stop_sequence = s.stop_sequence
        ) as n_stopped,
        max(e.n) filter (where e.dist_m < s.eff_dist_m) as n_before,
        min(e.n) filter (where e.dist_m >= s.eff_dist_m) as n_after
    from stops s
    join ev e on e.trip_instance_key = s.trip_instance_key and e.segment = s.segment
    group by s.trip_instance_key, s.segment, s.stop_sequence
),

estimated as (
    select
        s.trip_instance_key,
        s.segment,
        s.stop_sequence,
        s.service_date,
        s.trip_id,
        s.line,
        s.direction_id,
        s.stop_id,
        s.dist_m,
        s.eff_dist_m,
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
        st.fetched_at as interval_end_fetched_at
    from stops s
    join located l
        on l.trip_instance_key = s.trip_instance_key
        and l.segment = s.segment
        and l.stop_sequence = s.stop_sequence
    join (select distinct trip_instance_key, segment, vehicle_id from ev) v
        on v.trip_instance_key = s.trip_instance_key and v.segment = s.segment
    left join ev st on st.trip_instance_key = s.trip_instance_key and st.segment = s.segment and st.n = l.n_stopped
    left join ev pv on pv.trip_instance_key = s.trip_instance_key and pv.segment = s.segment and pv.n = l.n_stopped - 1
),

observed as (
    select
        *,
        interval_start_ts + to_microseconds(cast(round(
            1e6 * (epoch(interval_end_ts) - epoch(interval_start_ts)) / 2
        ) as bigint)) as observed_ts
    from estimated
    where method = 'observed'
),

-- the chain of known (distance, time) points of a run: observed midpoints and raw observations.
-- ts_mono is the running maximum over distance, so the chain never goes back in time.
anchors as (
    select
        trip_instance_key,
        segment,
        dist_m,
        prio,
        ts,
        max(ts) over w as ts_mono,
        max(fetched_at) over w as known_mono
    from (
        select trip_instance_key, segment, eff_dist_m as dist_m, 0 as prio, observed_ts as ts,
            interval_end_fetched_at as fetched_at
        from observed
        union all
        select trip_instance_key, segment, dist_m, 1 as prio, event_ts as ts, fetched_at from ev
    )
    window w as (
        partition by trip_instance_key, segment order by dist_m, prio, ts
        rows between unbounded preceding and current row
    )
),

bracketed as (
    select e.trip_instance_key, e.segment, e.stop_sequence, lo.ts_mono as lo_ts, lo.dist_m as lo_dist_m,
        up.ts_mono as up_ts, up.dist_m as up_dist_m,
        greatest(lo.known_mono, up.known_mono) as known_at
    from estimated e
    join lateral (
        select a.ts_mono, a.known_mono, a.dist_m
        from anchors a
        where a.trip_instance_key = e.trip_instance_key and a.segment = e.segment and a.dist_m < e.eff_dist_m
        order by a.dist_m desc, a.prio desc, a.ts desc
        limit 1
    ) lo on true
    join lateral (
        select a.ts_mono, a.known_mono, a.dist_m
        from anchors a
        where a.trip_instance_key = e.trip_instance_key and a.segment = e.segment and a.dist_m >= e.eff_dist_m
        order by a.dist_m, a.prio, a.ts
        limit 1
    ) up on true
    where e.method = 'bracketed'
),

timed as (
    select
        e.*,
        case e.method
            when 'observed' then o.observed_ts
            when 'bracketed' then b.lo_ts + to_microseconds(cast(round(
                1e6 * (epoch(b.up_ts) - epoch(b.lo_ts)) * (e.eff_dist_m - b.lo_dist_m)
                    / (b.up_dist_m - b.lo_dist_m)
            ) as bigint))
        end as arrival_ts,
        case e.method when 'observed' then e.interval_end_fetched_at when 'bracketed' then b.known_at end as own_known_at,
        case when e.method = 'observed' then epoch(e.interval_end_ts) - epoch(e.interval_start_ts) end as interval_width_s,
        case when e.method = 'bracketed' then epoch(b.up_ts) - epoch(b.lo_ts) end as bracket_gap_s
    from estimated e
    left join observed o
        on o.trip_instance_key = e.trip_instance_key and o.segment = e.segment and o.stop_sequence = e.stop_sequence
    left join bracketed b
        on b.trip_instance_key = e.trip_instance_key and b.segment = e.segment and b.stop_sequence = e.stop_sequence
),

-- compare each reconstructed stop with the neighbouring reconstructed stops of its run
checked as (
    select
        *,
        arrival_ts < lag(arrival_ts ignore nulls) over w
            or arrival_ts > lead(arrival_ts ignore nulls) over w as is_out_of_order,
        -- the order flag and the confidence also depend on the neighbouring reconstructed stops of
        -- the run, so the row is known only once those are
        greatest(
            own_known_at,
            lag(own_known_at ignore nulls) over w,
            lead(own_known_at ignore nulls) over w
        ) as known_at
    from timed
    window w as (partition by trip_instance_key, segment order by stop_sequence)
)

select
    trip_instance_key,
    segment,
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
    scheduled_arrival_ts,
    case when arrival_ts is not null then known_at end as known_at
from checked
