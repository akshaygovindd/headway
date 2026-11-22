-- One row per predicted stop time per capture. Predicted and scheduled times
-- are separate columns; a prediction is never treated as an observed arrival.
with predictions as (
    select
        t.trip_instance_key,
        t.stop_sequence,
        t.capture_id,
        t.run_id,
        t.fetched_at as collected_at,
        t.service_date,
        t.trip_id,
        t.stop_id,
        t.route_id,
        t.direction_id,
        t.predicted_arrival_ts,
        t.uncertainty,
        t.schedule_relationship,
        coalesce(c.is_duplicate_capture, false) as is_duplicate_capture,
        (t.schedule_relationship = 'ADDED' or t.trip_id like 'ADDED-%') as is_added_trip
    from {{ ref('stg_trip_updates') }} t
    left join {{ ref('int_captures') }} c on c.capture_id = t.capture_id
),

-- previous snapshot of the same trip stop, among non-duplicate captures only
prior as (
    select
        trip_instance_key,
        stop_sequence,
        capture_id,
        lag(predicted_arrival_ts) over (
            partition by trip_instance_key, stop_sequence order by collected_at, capture_id
        ) as prior_predicted_arrival_ts
    from predictions
    where not is_duplicate_capture
)

select
    p.trip_instance_key,
    p.stop_sequence,
    p.capture_id,
    p.run_id,
    p.collected_at,
    p.service_date,
    p.trip_id,
    p.stop_id,
    coalesce(s.line, p.route_id) as line,
    coalesce(s.direction_id, p.direction_id) as direction_id,
    s.scheduled_arrival_ts,
    s.hour_local as scheduled_hour_local,
    p.predicted_arrival_ts,
    epoch(p.predicted_arrival_ts) - epoch(s.scheduled_arrival_ts) as predicted_deviation_s,
    abs(epoch(p.predicted_arrival_ts) - epoch(s.scheduled_arrival_ts)) > 3600 as is_implausible_deviation,
    epoch(p.predicted_arrival_ts) - epoch(p.collected_at) as horizon_s,
    pr.prior_predicted_arrival_ts,
    epoch(p.predicted_arrival_ts) - epoch(pr.prior_predicted_arrival_ts) as revision_s,
    p.uncertainty,
    p.is_duplicate_capture,
    s.trip_instance_key is not null as schedule_matched,
    p.schedule_relationship,
    p.is_added_trip
from predictions p
left join prior pr
    on pr.trip_instance_key = p.trip_instance_key
    and pr.stop_sequence = p.stop_sequence
    and pr.capture_id = p.capture_id
left join {{ ref('int_scheduled_stop_times') }} s
    on s.trip_id = p.trip_id
    and s.service_date = p.service_date
    and s.stop_sequence = p.stop_sequence
    and s.stop_id = p.stop_id
