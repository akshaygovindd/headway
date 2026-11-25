-- What happened to each countdown of MBTA's published predictions (int_countdown_snapshots).
--
-- arrived:     a high or medium confidence reconstructed arrival exists (the run closest to the
--              schedule when a trip id was reused); hit_60 / hit_120 say whether it was within
--              60 / 120 s of the predicted time.
-- non_arrival: no such arrival, and the train confirmably never came: the feed marked the stop
--              SKIPPED in a snapshot at or after the countdown ('skipped'), or the vehicle was
--              observed on another trip after its last observation of this one, having never got
--              past the previous stop ('vehicle_ended_trip'). Counted as a miss. The ingest keeps
--              stop-level relationships only, so a trip-level CANCELED with no stop updates is not
--              visible here.
-- unknown:     neither an arrival nor a confirmed non-arrival (the vehicle was not seen, or the
--              arrival is low confidence). Excluded from the accuracy and counted beside it.
--
-- Hour flags are those of the hour of the predicted arrival, the same for every outcome.
with vehicle_trips as (
    select
        vehicle_id,
        trip_instance_key,
        current_stop_sequence,
        lead(trip_instance_key) over (partition by vehicle_id order by event_ts, capture_id)
            as next_trip_instance_key
    from {{ ref('int_vehicle_stop_events') }}
),

vehicle_ends as (
    select
        trip_instance_key,
        max(current_stop_sequence) as max_stop_sequence,
        coalesce(bool_or(
            next_trip_instance_key is not null and next_trip_instance_key <> trip_instance_key
        ), false) as ended_for_another_trip
    from vehicle_trips
    group by trip_instance_key
),

skips as (
    select trip_instance_key, stop_sequence, max(collected_at) as last_skipped_at
    from {{ ref('fct_stop_predictions') }}
    where schedule_matched and not is_duplicate_capture and schedule_relationship = 'SKIPPED'
    group by trip_instance_key, stop_sequence
),

joined as (
    select
        c.trip_instance_key,
        c.stop_sequence,
        c.countdown_min,
        c.capture_id,
        c.collected_at,
        c.countdown_s,
        c.service_date,
        c.line,
        c.direction_id,
        c.stop_id,
        st.station_id,
        st.station_name,
        c.predicted_arrival_ts,
        {{ service_date_from_ts('c.predicted_arrival_ts') }} as predicted_service_date,
        cast(extract(hour from c.predicted_arrival_ts at time zone '{{ var("timezone") }}') as integer) as hour_local,
        a.arrival_ts,
        epoch(c.predicted_arrival_ts) - epoch(a.arrival_ts) as error_s,
        a.trip_instance_key is not null as has_arrival,
        coalesce(k.last_skipped_at >= c.collected_at, false) as is_skipped,
        coalesce(
            v.ended_for_another_trip and v.max_stop_sequence < c.stop_sequence, false
        ) as is_vehicle_ended
    from {{ ref('int_countdown_snapshots') }} c
    left join {{ ref('int_usable_arrivals') }} a
        on a.trip_instance_key = c.trip_instance_key
        and a.stop_sequence = c.stop_sequence
        and a.is_nearest_segment
    left join skips k on k.trip_instance_key = c.trip_instance_key and k.stop_sequence = c.stop_sequence
    left join vehicle_ends v on v.trip_instance_key = c.trip_instance_key
    left join {{ ref('int_stop_stations') }} st on st.stop_id = c.stop_id
),

classified as (
    select
        *,
        case
            when has_arrival then 'arrived'
            when is_skipped or is_vehicle_ended then 'non_arrival'
            else 'unknown'
        end as outcome
    from joined
)

select
    c.trip_instance_key,
    c.stop_sequence,
    c.countdown_min,
    c.capture_id,
    c.collected_at,
    c.countdown_s,
    c.service_date,
    c.line,
    c.direction_id,
    c.stop_id,
    coalesce(c.station_id, c.stop_id) as station_id,
    coalesce(c.station_name, c.stop_id) as station_name,
    c.predicted_arrival_ts,
    c.predicted_service_date,
    c.hour_local,
    c.arrival_ts,
    c.error_s,
    c.outcome,
    case
        when c.outcome <> 'non_arrival' then null
        when c.is_skipped then 'skipped'
        else 'vehicle_ended_trip'
    end as non_arrival_reason,
    case c.outcome when 'arrived' then abs(c.error_s) <= 60 when 'non_arrival' then false end as hit_60,
    case c.outcome when 'arrived' then abs(c.error_s) <= 120 when 'non_arrival' then false end as hit_120,
    coalesce(f.data_outage, false) as data_outage,
    d.line is not null as service_disruption
from classified c
left join {{ ref('int_hour_flags') }} f
    on f.service_date = c.predicted_service_date and f.hour_local = c.hour_local
left join {{ ref('int_disrupted_line_hours') }} d
    on d.line = c.line and d.service_date = c.predicted_service_date and d.hour_local = c.hour_local
