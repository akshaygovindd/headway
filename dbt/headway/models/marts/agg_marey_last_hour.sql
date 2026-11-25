-- Reconstructed arrivals of the last complete hour, for the Marey (time-distance) chart of a line
-- page (Release 4). The hour is the one before the latest hour of the export: hour_start is
-- max(int_window_hours.hour_start) minus one hour, because the latest hour is still running at
-- metrics_as_of. One row per line, direction, run of a trip instance and stop whose arrival_ts
-- falls inside [hour_start, hour_start + 1 hour). method is observed (the vehicle was seen at the
-- stop) or estimated (the reconstruction's bracketed arrival, interpolated along the shape);
-- stops with no arrival ('missing') are dropped. Small by design: one hour of trips.
with latest as (
    select max(hour_start) - interval 1 hour as hour_start
    from {{ ref('int_window_hours') }}
)

select
    r.line,
    r.direction_id,
    r.trip_instance_key,
    r.segment,
    r.trip_id,
    r.stop_id,
    s.stop_name,
    r.stop_sequence,
    r.arrival_ts,
    case r.method when 'observed' then 'observed' else 'estimated' end as method,
    r.confidence,
    r.scheduled_arrival_ts as scheduled_ts,
    l.hour_start
from {{ ref('fct_reconstructed_arrivals') }} r
cross join latest l
left join {{ ref('dim_stop') }} s on s.stop_id = r.stop_id
where r.method in ('observed', 'bracketed')
    and r.arrival_ts is not null
    and r.arrival_ts >= l.hour_start
    and r.arrival_ts < l.hour_start + interval 1 hour
