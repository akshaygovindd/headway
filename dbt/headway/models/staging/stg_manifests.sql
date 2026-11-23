-- One row per capture attempt recorded in an ingest run manifest. Duplicates (a part written
-- twice by overlapping curation or backfill runs) collapse on run_id x capture_id.
select
    capture_id,
    run_id,
    city_id,
    feed,
    scheduled_at,
    fetched_at,
    status,
    http_status,
    bytes,
    entity_count,
    content_hash,
    feed_header_ts,
    header_parse_error,
    error
from {{ source('curated', 'manifests') }}
{{ lookback_filter() }}
qualify row_number() over (partition by run_id, capture_id order by fetched_at) = 1
