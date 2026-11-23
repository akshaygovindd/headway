-- One row per capture attempt. is_duplicate_capture marks a capture whose
-- payload hash equals the previous successful capture of the same feed. With 15 s sampling a
-- minute holds up to four vehicle_positions captures: they share scheduled_at, hence one
-- capture_minute, and are ordered by poll_index within it.
with ok as (
    select
        capture_id,
        content_hash = lag(content_hash) over (
            partition by feed order by scheduled_at, poll_index, fetched_at, capture_id
        ) as is_duplicate_capture
    from {{ ref('stg_manifests') }}
    where status = 'ok'
)

select
    m.capture_id,
    m.run_id,
    m.feed,
    m.scheduled_at,
    m.poll_index,
    date_trunc('minute', m.scheduled_at) as capture_minute,
    m.fetched_at,
    m.status,
    m.content_hash,
    m.entity_count,
    m.bytes,
    coalesce(ok.is_duplicate_capture, false) as is_duplicate_capture,
    {{ service_date_from_ts('m.scheduled_at') }} as service_date,
    cast(extract(hour from m.scheduled_at at time zone '{{ var("timezone") }}') as integer) as hour_local
from {{ ref('stg_manifests') }} m
left join ok on ok.capture_id = m.capture_id
