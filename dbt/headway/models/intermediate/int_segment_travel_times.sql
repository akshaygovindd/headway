-- Travel time of each scheduled stop-to-stop segment of each trip (Release 4, Phase 4.5; METRICS.md
-- "Slow segments"). Segment: a stop and the next stop of the scheduled trip, one direction, with the
-- platform stop ids of agg_headway_segment_window. travel_s is the arrival at the next stop minus
-- the arrival at the stop for the same run, both high/medium (int_usable_arrivals); null when the
-- trip was not measured on the segment. One row per scheduled trip and segment. Flags are those of
-- the local hour of the scheduled arrival at the from stop.
with pairs as (
    select
        trip_instance_key,
        service_date,
        line,
        direction_id,
        stop_id as from_stop_id,
        lead(stop_id) over w as to_stop_id,
        stop_sequence as from_seq,
        lead(stop_sequence) over w as to_seq,
        scheduled_arrival_ts as from_sched_ts,
        hour_local
    from {{ ref('int_scheduled_stop_times') }}
    window w as (partition by trip_instance_key order by stop_sequence)
),

measured as (
    select
        x.trip_instance_key,
        x.stop_sequence as from_seq,
        y.stop_sequence as to_seq,
        x.arrival_ts as from_ts,
        epoch(y.arrival_ts) - epoch(x.arrival_ts) as travel_s
    from {{ ref('int_usable_arrivals') }} x
    join {{ ref('int_usable_arrivals') }} y
        on y.trip_instance_key = x.trip_instance_key and y.segment = x.segment
    join pairs p
        on p.trip_instance_key = x.trip_instance_key and p.from_seq = x.stop_sequence and p.to_seq = y.stop_sequence
    qualify row_number() over (
        partition by x.trip_instance_key, x.stop_sequence
        order by abs(epoch(x.arrival_ts) - epoch(x.scheduled_arrival_ts)), x.segment
    ) = 1
)

select
    p.trip_instance_key,
    p.service_date,
    p.line,
    p.direction_id,
    p.from_stop_id,
    p.to_stop_id,
    {{ time_band('p.from_sched_ts') }} as time_band,
    p.hour_local,
    m.from_ts,
    m.travel_s,
    coalesce(f.data_outage, false) as data_outage,
    d.line is not null as service_disruption
from pairs p
left join measured m
    on m.trip_instance_key = p.trip_instance_key and m.from_seq = p.from_seq and m.to_seq = p.to_seq
join {{ ref('int_hour_flags') }} f on f.service_date = p.service_date and f.hour_local = p.hour_local
left join {{ ref('int_disrupted_line_hours') }} d
    on d.line = p.line and d.service_date = p.service_date and d.hour_local = p.hour_local
where p.to_stop_id is not null
