-- Prediction error of the feed against the reconstructed arrival: for each trip stop and horizon
-- bucket, the last snapshot in the bucket. Horizon is predicted_arrival_ts - collected_at (what a
-- rider saw, independent of the reconstruction). Buckets are '0-5', '5-10', '10-20' and '20-30'
-- minutes, the lower bound included. Only snapshots collected at or before the reconstructed
-- arrival count; arrivals are high or medium confidence. error_s is predicted minus
-- reconstructed (positive: the prediction was late). data_outage and service_disruption are the
-- flags of the arrival hour (scheduled_hour_local is the hour of the trip stop's scheduled
-- arrival, the one coverage is counted by); outage rows stay here and are left out of the aggregate.
with snapshots as (
    select
        a.trip_instance_key,
        a.segment,
        a.stop_sequence,
        a.service_date,
        a.line,
        a.direction_id,
        a.stop_id,
        a.arrival_service_date,
        a.arrival_hour_local as hour_local,
        a.sched_hour_local as scheduled_hour_local,
        a.data_outage,
        a.service_disruption,
        a.confidence,
        a.arrival_ts,
        p.capture_id,
        p.collected_at,
        p.horizon_s,
        p.predicted_arrival_ts,
        case
            when p.horizon_s < 300 then '0-5'
            when p.horizon_s < 600 then '5-10'
            when p.horizon_s < 1200 then '10-20'
            else '20-30'
        end as horizon_bucket
    from {{ ref('fct_stop_predictions') }} p
    join {{ ref('int_usable_arrivals') }} a
        on a.trip_instance_key = p.trip_instance_key
        and a.stop_sequence = p.stop_sequence
        and a.is_nearest_segment
    where p.schedule_matched
        and not p.is_duplicate_capture
        and p.schedule_relationship not in ('SKIPPED', 'NO_DATA')
        and p.predicted_arrival_ts is not null
        and p.horizon_s between 0 and 1800
        and p.collected_at <= a.arrival_ts
),

picked as (
    select *
    from snapshots
    qualify row_number() over (
        partition by trip_instance_key, stop_sequence, horizon_bucket
        order by collected_at desc, capture_id desc
    ) = 1
)

select
    trip_instance_key,
    segment,
    stop_sequence,
    horizon_bucket,
    service_date,
    line,
    direction_id,
    stop_id,
    arrival_service_date,
    hour_local,
    scheduled_hour_local,
    capture_id,
    collected_at,
    horizon_s,
    predicted_arrival_ts,
    arrival_ts,
    confidence,
    epoch(predicted_arrival_ts) - epoch(arrival_ts) as error_s,
    abs(epoch(predicted_arrival_ts) - epoch(arrival_ts)) as abs_error_s,
    data_outage,
    service_disruption
from picked
