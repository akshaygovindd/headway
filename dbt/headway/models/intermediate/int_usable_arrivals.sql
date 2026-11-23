-- High and medium confidence reconstructed arrivals that match a scheduled stop: the only
-- arrivals the Release 2 metrics use. Several runs of a reused trip id can reach one trip stop;
-- is_nearest_segment marks the run whose arrival is closest to the schedule, the one metrics
-- 6, 7 and 9 use. Flags are those of the local hour of the arrival.
with usable as (
    select
        r.trip_instance_key,
        r.segment,
        r.stop_sequence,
        r.service_date,
        r.trip_id,
        r.line,
        r.direction_id,
        r.stop_id,
        r.vehicle_id,
        r.arrival_ts,
        r.method,
        r.confidence,
        r.scheduled_arrival_ts,
        {{ service_date_from_ts('r.arrival_ts') }} as arrival_service_date,
        cast(extract(hour from r.arrival_ts at time zone '{{ var("timezone") }}') as integer) as arrival_hour_local,
        cast(extract(hour from r.scheduled_arrival_ts at time zone '{{ var("timezone") }}') as integer) as sched_hour_local,
        epoch(r.arrival_ts) - epoch(r.scheduled_arrival_ts) as deviation_s,
        row_number() over (
            partition by r.trip_instance_key, r.stop_sequence
            order by abs(epoch(r.arrival_ts) - epoch(r.scheduled_arrival_ts)), r.segment
        ) = 1 as is_nearest_segment
    from {{ ref('fct_reconstructed_arrivals') }} r
    where r.confidence in ('high', 'medium')
        and r.arrival_ts is not null
        and r.scheduled_arrival_ts is not null
)

select
    u.*,
    coalesce(f.data_outage, false) as data_outage,
    d.line is not null as service_disruption
from usable u
left join {{ ref('int_hour_flags') }} f
    on f.service_date = u.arrival_service_date and f.hour_local = u.arrival_hour_local
left join {{ ref('int_disrupted_line_hours') }} d
    on d.line = u.line and d.service_date = u.arrival_service_date and d.hour_local = u.arrival_hour_local
