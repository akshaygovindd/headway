-- One row per ingest run: how each feed fetched, what it contained, and what was
-- quarantined from the run's captures.
with feeds as (
    select
        run_id,
        min(scheduled_at) as scheduled_at,
        {% for feed in ['trip_updates', 'vehicle_positions', 'alerts'] %}
        max(status) filter (where feed = '{{ feed }}') as {{ feed }}_status,
        max(bytes) filter (where feed = '{{ feed }}') as {{ feed }}_bytes,
        max(entity_count) filter (where feed = '{{ feed }}') as {{ feed }}_entity_count,
        bool_or(is_duplicate_capture) filter (where feed = '{{ feed }}') as {{ feed }}_is_duplicate,
        {% endfor %}
        count(*) as n_captures
    from {{ ref('int_captures') }}
    group by run_id
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
left join vehicles v on v.run_id = f.run_id
left join quarantined q on q.run_id = f.run_id
