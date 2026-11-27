-- The countdowns a rider could have seen, from MBTA's published predictions: for each trip stop and
-- countdown bucket, the one snapshot whose countdown (predicted_arrival_ts - collected_at) is
-- closest to the bucket centre. Buckets are centred on 1, 3, 5 and 10 minutes and take countdowns
-- within 30 s of the centre (they do not overlap). Ties go to the later snapshot. Only
-- schedule-matched snapshots of non-duplicate captures with a predicted arrival count; SKIPPED
-- and NO_DATA rows carry none. Unlike the prediction errors, a snapshot collected after the
-- train arrived is kept: a countdown still showing then is a miss.
with snapshots as (
    select
        p.trip_instance_key,
        p.stop_sequence,
        p.capture_id,
        p.collected_at,
        p.service_date,
        p.scheduled_hour_local,
        p.line,
        p.direction_id,
        p.stop_id,
        p.predicted_arrival_ts,
        p.horizon_s as countdown_s,
        b.countdown_min
    from {{ ref('fct_stop_predictions') }} p
    join (values (1, 60), (3, 180), (5, 300), (10, 600)) as b (countdown_min, centre_s)
        on abs(p.horizon_s - b.centre_s) <= 30
    where p.schedule_matched
        and not p.is_duplicate_capture
        and p.schedule_relationship not in ('SKIPPED', 'NO_DATA')
        and p.predicted_arrival_ts is not null
)

select
    *,
    abs(countdown_s - case countdown_min when 1 then 60 when 3 then 180 when 5 then 300 else 600 end)
        as centre_distance_s
from snapshots
qualify row_number() over (
    partition by trip_instance_key, stop_sequence, countdown_min
    order by
        abs(countdown_s - case countdown_min when 1 then 60 when 3 then 180 when 5 then 300 else 600 end),
        collected_at desc,
        capture_id desc
) = 1
