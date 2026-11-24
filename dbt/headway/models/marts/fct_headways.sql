-- One row per candidate headway: a high or medium confidence train B and the high or medium train
-- A before it at the same stop (same line, direction and service date). status says whether the
-- pair is a usable headway:
--   valid                a usable headway
--   train_between        another train of the line and direction has an arrival time between A and
--                        B at the stop, or at a neighbouring stop (any confidence)
--   unproven_order       no neighbouring stop where both A and B have a time, so the order cannot
--                        be proven against a train in between
--   no_scheduled_headway B is the first scheduled train at the stop (or two share a time)
--   implausible          actual headway over 3 h or scheduled headway over 2 h
-- The neighbouring stops are the stops before and after the stop in B's run. A pair is proven
-- when at least one neighbouring stop has both A and B, A first, and no other train between them.
-- A train that was never reconstructed anywhere cannot be seen; see METRICS.md.
-- hour_local and service_date are those of B's scheduled arrival. known_at is when both arrivals
-- were known (fct_reconstructed_arrivals.known_at); features of a forecast at t use known_at <= t.
with all_arrivals as (
    select
        line, direction_id, service_date, stop_id,
        trip_instance_key, segment, stop_sequence,
        arrival_ts, confidence, scheduled_arrival_ts, known_at
    from {{ ref('fct_reconstructed_arrivals') }}
    where arrival_ts is not null
),

-- predecessor among all arrivals (any confidence) at the stop
ordered_all as (
    select
        *,
        lag(trip_instance_key) over w as prev_all_trip,
        lag(segment) over w as prev_all_segment
    from all_arrivals
    window w as (
        partition by line, direction_id, service_date, stop_id
        order by arrival_ts, trip_instance_key, segment
    )
),

-- predecessor among high and medium arrivals
paired as (
    select
        *,
        lag(trip_instance_key) over w as a_trip,
        lag(segment) over w as a_segment,
        lag(arrival_ts) over w as a_arrival_ts,
        lag(known_at) over w as lag_known_at
    from ordered_all
    where confidence in ('high', 'medium')
    window w as (
        partition by line, direction_id, service_date, stop_id
        order by arrival_ts, trip_instance_key, segment
    )
),

pairs as (
    select
        p.line, p.direction_id, p.service_date, p.stop_id,
        p.a_trip, p.a_segment, p.a_arrival_ts,
        p.trip_instance_key as b_trip, p.segment as b_segment, p.stop_sequence as b_stop_sequence,
        p.arrival_ts as b_arrival_ts,
        p.scheduled_arrival_ts as b_scheduled_arrival_ts,
        p.known_at as b_known_at,
        p.lag_known_at as a_known_at,
        not (p.prev_all_trip = p.a_trip and p.prev_all_segment = p.a_segment) as train_between_at_stop,
        n.prev_stop_id,
        n.next_stop_id
    from paired p
    join (
        select
            trip_instance_key, segment, stop_sequence,
            lag(stop_id) over w as prev_stop_id,
            lead(stop_id) over w as next_stop_id
        from {{ ref('fct_reconstructed_arrivals') }}
        window w as (partition by trip_instance_key, segment order by stop_sequence)
    ) n
        on n.trip_instance_key = p.trip_instance_key
        and n.segment = p.segment
        and n.stop_sequence = p.stop_sequence
    where p.a_arrival_ts is not null
),

neighbours as (
    select *, prev_stop_id as n_stop from pairs where prev_stop_id is not null
    union all
    select *, next_stop_id as n_stop from pairs where next_stop_id is not null
),

-- order of A and B at each neighbouring stop, and trains between them there
proofs as (
    select
        n.line, n.direction_id, n.service_date, n.stop_id, n.b_trip, n.b_segment,
        case
            when a.arrival_ts >= b.arrival_ts then 'unproven'
            when exists (
                select 1
                from all_arrivals x
                where x.line = n.line
                    and x.direction_id = n.direction_id
                    and x.service_date = n.service_date
                    and x.stop_id = n.n_stop
                    and x.arrival_ts > a.arrival_ts
                    and x.arrival_ts < b.arrival_ts
                    and not (x.trip_instance_key = n.a_trip and x.segment = n.a_segment)
                    and not (x.trip_instance_key = n.b_trip and x.segment = n.b_segment)
            ) then 'between'
            else 'proven'
        end as proof
    from neighbours n
    join all_arrivals a
        on a.line = n.line and a.direction_id = n.direction_id and a.service_date = n.service_date
        and a.stop_id = n.n_stop and a.trip_instance_key = n.a_trip and a.segment = n.a_segment
    join all_arrivals b
        on b.line = n.line and b.direction_id = n.direction_id and b.service_date = n.service_date
        and b.stop_id = n.n_stop and b.trip_instance_key = n.b_trip and b.segment = n.b_segment
),

proof_summary as (
    select
        line, direction_id, service_date, stop_id, b_trip, b_segment,
        bool_or(proof = 'between') as train_between_at_neighbour,
        bool_or(proof = 'proven') as is_proven
    from proofs
    group by 1, 2, 3, 4, 5, 6
),

-- scheduled headway at the stop: the previous scheduled train of the line and direction
scheduled as (
    select
        trip_instance_key,
        stop_sequence,
        epoch(scheduled_arrival_ts) - epoch(lag(scheduled_arrival_ts) over (
            partition by line, direction_id, service_date, stop_id
            order by scheduled_arrival_ts, trip_instance_key
        )) as scheduled_headway_s
    from {{ ref('int_scheduled_stop_times') }}
),

judged as (
    select
        p.*,
        coalesce(s.train_between_at_neighbour, false) as train_between_at_neighbour,
        coalesce(s.is_proven, false) as is_proven,
        epoch(p.b_arrival_ts) - epoch(p.a_arrival_ts) as actual_headway_s,
        sc.scheduled_headway_s
    from pairs p
    left join proof_summary s
        on s.line = p.line and s.direction_id is not distinct from p.direction_id
        and s.service_date = p.service_date and s.stop_id = p.stop_id
        and s.b_trip = p.b_trip and s.b_segment = p.b_segment
    left join scheduled sc on sc.trip_instance_key = p.b_trip and sc.stop_sequence = p.b_stop_sequence
)

select
    line,
    direction_id,
    service_date,
    stop_id,
    b_stop_sequence as stop_sequence,
    b_trip as to_trip_instance_key,
    b_segment as to_segment,
    a_trip as from_trip_instance_key,
    a_segment as from_segment,
    cast(extract(hour from b_scheduled_arrival_ts at time zone '{{ var("timezone") }}') as integer) as hour_local,
    a_arrival_ts as from_arrival_ts,
    b_arrival_ts as to_arrival_ts,
    -- known once the later train's arrival is known (the earlier train's is older); a train in
    -- between or a neighbouring-stop proof can only be settled by captures up to then
    greatest(b_known_at, a_known_at) as known_at,
    actual_headway_s,
    scheduled_headway_s,
    actual_headway_s / nullif(scheduled_headway_s, 0) as ratio,
    case
        when train_between_at_stop or train_between_at_neighbour then 'train_between'
        when not is_proven then 'unproven_order'
        when scheduled_headway_s is null or scheduled_headway_s <= 0 then 'no_scheduled_headway'
        when actual_headway_s > 10800 or scheduled_headway_s > 7200 then 'implausible'
        else 'valid'
    end as status,
    case
        when status <> 'valid' then null
        when actual_headway_s / scheduled_headway_s < 0.5 then 'bunched'
        when actual_headway_s / scheduled_headway_s > 1.5 then 'gap'
        else 'within'
    end as band
from judged
