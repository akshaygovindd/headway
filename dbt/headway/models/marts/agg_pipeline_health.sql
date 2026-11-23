-- One row per ingest run: how each feed fetched, what it contained, and what was
-- quarantined from the run's captures. The per-feed status, bytes, entity count and duplicate
-- columns describe poll 0 (every feed fetched once per run); the extra 15 s vehicle polls are
-- summarised in vehicle_polls_ok / vehicle_polls_error, and n_captures counts all captures.
with feeds as (
    select
        run_id,
        min(scheduled_at) as scheduled_at,
        {% for feed in ['trip_updates', 'vehicle_positions', 'alerts'] %}
        max(status) filter (where feed = '{{ feed }}' and poll_index = 0) as {{ feed }}_status,
        max(bytes) filter (where feed = '{{ feed }}' and poll_index = 0) as {{ feed }}_bytes,
        max(entity_count) filter (where feed = '{{ feed }}' and poll_index = 0) as {{ feed }}_entity_count,
        bool_or(is_duplicate_capture) filter (where feed = '{{ feed }}' and poll_index = 0) as {{ feed }}_is_duplicate,
        {% endfor %}
        count(*) filter (where feed = 'vehicle_positions' and status = 'ok') as vehicle_polls_ok,
        count(*) filter (where feed = 'vehicle_positions' and status <> 'ok') as vehicle_polls_error,
        count(*) as n_captures
    from {{ ref('int_captures') }}
    group by run_id
),

-- Extra deliveries per scheduled minute: every distinct run that recorded the minute (kept,
-- dropped by the capture_id dedupe, or flagged by ingest as a duplicate delivery) beyond the
-- first. Reported once, on the earliest kept run of the minute, so sums over time stay exact.
deliveries as (
    select
        min(run_id) filter (where delivery_rank = 1) as primary_run_id,
        count(distinct run_id) - 1 as n_extra
    from {{ ref('stg_manifest_deliveries') }}
    group by scheduled_minute
),

vehicles as (
    select run_id, count(distinct vehicle_id) as subway_vehicle_count
    from {{ ref('stg_vehicle_positions') }}
    group by run_id
),

quarantined as (
    select
        run_id,
        count(*) as quarantine_total,
        {% for reason in ['missing_trip_id', 'invalid_coordinates', 'protobuf_parse_error'] %}
        count(*) filter (where reason = '{{ reason }}') as quarantine_{{ reason }},
        {% endfor %}
        count(*) filter (where reason not in ('missing_trip_id', 'invalid_coordinates', 'protobuf_parse_error')) as quarantine_other
    from {{ ref('stg_quarantine') }}
    group by run_id
)

select
    f.run_id,
    f.scheduled_at,
    f.n_captures,
    f.vehicle_polls_ok,
    f.vehicle_polls_error,
    coalesce(d.n_extra, 0) as duplicate_delivery_count,
    {% for feed in ['trip_updates', 'vehicle_positions', 'alerts'] %}
    f.{{ feed }}_status,
    f.{{ feed }}_bytes,
    f.{{ feed }}_entity_count,
    coalesce(f.{{ feed }}_is_duplicate, false) as {{ feed }}_is_duplicate,
    {% endfor %}
    coalesce(v.subway_vehicle_count, 0) as subway_vehicle_count,
    coalesce(q.quarantine_total, 0) as quarantine_total,
    coalesce(q.quarantine_missing_trip_id, 0) as quarantine_missing_trip_id,
    coalesce(q.quarantine_invalid_coordinates, 0) as quarantine_invalid_coordinates,
    coalesce(q.quarantine_protobuf_parse_error, 0) as quarantine_protobuf_parse_error,
    coalesce(q.quarantine_other, 0) as quarantine_other
from feeds f
left join deliveries d on d.primary_run_id = f.run_id
left join vehicles v on v.run_id = f.run_id
left join quarantined q on q.run_id = f.run_id
