-- Observable scheduled trip stops with a usable observation: at least one schedule-matched,
-- non-duplicate snapshot that is not NO_DATA, collected 0 to 30 minutes before the
-- scheduled arrival. A SKIPPED stop counts as covered (it is an observed service
-- change); SKIPPED snapshots carry no predicted time, so their window is measured
-- from the scheduled arrival, other snapshots use horizon_s as for eligibility.
select
    p.trip_instance_key,
    p.stop_sequence,
    any_value(p.service_date) as service_date,
    any_value(p.line) as line,
    any_value(p.direction_id) as direction_id,
    any_value(p.stop_id) as stop_id,
    any_value(p.scheduled_hour_local) as hour_local,
    arg_max(p.schedule_relationship, p.collected_at) = 'SKIPPED' as is_skipped
from {{ ref('fct_stop_predictions') }} p
join {{ ref('int_observable_trip_stops') }} o
    on o.trip_instance_key = p.trip_instance_key and o.stop_sequence = p.stop_sequence
where p.schedule_matched
    and not p.is_duplicate_capture
    and p.schedule_relationship <> 'NO_DATA'
    and p.collected_at < p.scheduled_arrival_ts
    and case
        when p.schedule_relationship = 'SKIPPED'
            then epoch(p.scheduled_arrival_ts) - epoch(p.collected_at) <= 1800
        else p.horizon_s between 0 and 1800
    end
group by p.trip_instance_key, p.stop_sequence
