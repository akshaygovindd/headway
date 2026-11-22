-- One row per stop time update per capture. Duplicate rows within a capture on
-- capture_id x trip_id x stop_id x stop_sequence are collapsed to one.
with src as (
    select * from {{ source('curated', 'trip_updates') }}
    {{ lookback_filter() }}
),

deduped as (
    select
        *,
        row_number() over (
            partition by capture_id, trip_id, stop_id, stop_sequence
            order by predicted_arrival_ts desc nulls last, predicted_departure_ts desc nulls last
        ) as _rn
    from src
)

select
    capture_id,
    run_id,
    content_hash,
    fetched_at,
    feed_header_ts,
    trip_id,
    route_id,
    direction_id,
    start_date,
    start_time,
    coalesce(start_date, {{ service_date_from_ts('fetched_at') }}) as service_date,
    trip_id || '|' || coalesce(start_date, {{ service_date_from_ts('fetched_at') }})::varchar
        || '|' || coalesce(start_time, '') as trip_instance_key,
    vehicle_id,
    stop_id,
    stop_sequence,
    predicted_arrival_ts,
    predicted_departure_ts,
    uncertainty,
    schedule_relationship,
    event_ts
from deduped
where _rn = 1
