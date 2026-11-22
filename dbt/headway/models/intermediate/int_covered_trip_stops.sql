-- Scheduled trip stops with a usable observation: at least one schedule-matched,
-- non-duplicate snapshot that is not NO_DATA, collected 0 to 30 minutes before the
-- scheduled arrival. A SKIPPED stop counts as covered (it is an observed service
-- change); SKIPPED snapshots carry no predicted time, so their window is measured
-- from the scheduled arrival, other snapshots use horizon_s as for eligibility.
select
    trip_instance_key,
    stop_sequence,
    any_value(service_date) as service_date,
    any_value(line) as line,
    any_value(direction_id) as direction_id,
    any_value(stop_id) as stop_id,
    any_value(scheduled_hour_local) as hour_local,
    arg_max(schedule_relationship, collected_at) = 'SKIPPED' as is_skipped
from {{ ref('fct_stop_predictions') }}
where schedule_matched
    and not is_duplicate_capture
    and schedule_relationship <> 'NO_DATA'
    and collected_at < scheduled_arrival_ts
    and case
        when schedule_relationship = 'SKIPPED'
            then epoch(scheduled_arrival_ts) - epoch(collected_at) <= 1800
        else horizon_s between 0 and 1800
    end
group by trip_instance_key, stop_sequence
