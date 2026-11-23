{# Trip-level ETT rows shared by agg_ett_daily and agg_ett_window: one row per scheduled trip
   with its time band, day type, outage and disruption flags and ett_s (null when not measured). #}
{% macro ett_trips() %}
with ranked as (
    select
        trip_instance_key,
        service_date,
        line,
        direction_id,
        stop_sequence,
        scheduled_arrival_ts,
        hour_local,
        row_number() over (partition by trip_instance_key order by stop_sequence) as rn,
        count(*) over (partition by trip_instance_key) as n_stops
    from {{ ref('int_scheduled_stop_times') }}
),

trips as (
    select
        a.trip_instance_key,
        a.service_date,
        a.line,
        a.direction_id,
        a.stop_sequence as first_seq,
        a.scheduled_arrival_ts as first_sched_ts,
        a.hour_local as first_hour,
        b.stop_sequence as last_seq,
        b.scheduled_arrival_ts as last_sched_ts,
        b.hour_local as last_hour
    from ranked a
    join ranked b on b.trip_instance_key = a.trip_instance_key and b.rn = b.n_stops - 1
    where a.rn = 2 and a.n_stops >= 4
),

measured as (
    select
        t.trip_instance_key,
        (epoch(y.arrival_ts) - epoch(x.arrival_ts))
            - (epoch(t.last_sched_ts) - epoch(t.first_sched_ts)) as ett_s
    from trips t
    join {{ ref('int_usable_arrivals') }} x
        on x.trip_instance_key = t.trip_instance_key and x.stop_sequence = t.first_seq
    join {{ ref('int_usable_arrivals') }} y
        on y.trip_instance_key = t.trip_instance_key and y.stop_sequence = t.last_seq and y.segment = x.segment
    qualify row_number() over (
        partition by t.trip_instance_key
        order by abs(epoch(x.arrival_ts) - epoch(t.first_sched_ts)), x.segment
    ) = 1
),

flagged as (
    select
        t.trip_instance_key,
        t.first_hour,
        t.line,
        t.direction_id,
        t.service_date,
        {{ time_band('t.first_sched_ts') }} as time_band,
        case when dayofweek(t.service_date) in (0, 6) then 'weekend' else 'weekday' end as day_type,
        coalesce(f1.data_outage, false) or coalesce(f2.data_outage, false) as data_outage,
        d1.line is not null or d2.line is not null as service_disruption,
        m.ett_s
    from trips t
    join {{ ref('int_hour_flags') }} f1 on f1.service_date = t.service_date and f1.hour_local = t.first_hour
    join {{ ref('int_hour_flags') }} f2 on f2.service_date = t.service_date and f2.hour_local = t.last_hour
    left join {{ ref('int_disrupted_line_hours') }} d1
        on d1.line = t.line and d1.service_date = t.service_date and d1.hour_local = t.first_hour
    left join {{ ref('int_disrupted_line_hours') }} d2
        on d2.line = t.line and d2.service_date = t.service_date and d2.hour_local = t.last_hour
    left join measured m on m.trip_instance_key = t.trip_instance_key
)
select * from flagged
{% endmacro %}
