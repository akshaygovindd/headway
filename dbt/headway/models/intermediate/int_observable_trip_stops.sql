-- Scheduled trip stops we could have observed: at least one ok, non-duplicate
-- trip_updates capture fetched within 30 minutes up to the scheduled arrival.
-- Stops scheduled while the pipeline was down are left out, so coverage measures
-- feed completeness during observed periods; outages show up in missed_runs.
select
    s.trip_instance_key,
    s.stop_sequence,
    s.service_date,
    s.line,
    s.direction_id,
    s.stop_id,
    s.hour_local,
    s.scheduled_arrival_ts
from {{ ref('int_scheduled_stop_times') }} s
where exists (
    select 1
    from {{ ref('int_captures') }} c
    where c.feed = 'trip_updates'
        and c.status = 'ok'
        and not c.is_duplicate_capture
        and c.fetched_at >= s.scheduled_arrival_ts - interval 30 minute
        and c.fetched_at <= s.scheduled_arrival_ts
)
