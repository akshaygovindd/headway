-- The eligible prediction of each scheduled trip stop: the last snapshot
-- collected before the scheduled arrival that is schedule matched, from a
-- non-duplicate capture, not SKIPPED or NO_DATA, and 0 to 30 minutes ahead.
with candidates as (
    select *
    from {{ ref('fct_stop_predictions') }}
    where schedule_matched
        and not is_duplicate_capture
        and schedule_relationship not in ('SKIPPED', 'NO_DATA')
        and predicted_arrival_ts is not null
        and horizon_s between 0 and 1800
        and collected_at < scheduled_arrival_ts
)

select
    trip_instance_key,
    stop_sequence,
    capture_id,
    collected_at,
    service_date,
    line,
    direction_id,
    stop_id,
    scheduled_hour_local as hour_local,
    scheduled_arrival_ts,
    predicted_arrival_ts,
    predicted_deviation_s,
    horizon_s,
    revision_s
from candidates
qualify row_number() over (
    partition by trip_instance_key, stop_sequence order by collected_at desc, capture_id desc
) = 1
