-- One row per capture attempt recorded in an ingest run manifest.
with runs as (
    select run_id, unnest(entries) as e
    from {{ source('curated', 'manifests') }}
)

select
    e.capture_id as capture_id,
    run_id,
    e.city_id as city_id,
    e.feed as feed,
    e.scheduled_at as scheduled_at,
    e.fetched_at as fetched_at,
    e.status as status,
    e.http_status as http_status,
    e.bytes as bytes,
    e.entity_count as entity_count,
    e.content_hash as content_hash,
    e.feed_header_ts as feed_header_ts,
    e.header_parse_error as header_parse_error,
    e.error as error
from runs
