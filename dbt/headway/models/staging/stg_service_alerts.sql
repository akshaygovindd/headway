-- One row per informed entity of an alert per capture.
select
    capture_id,
    run_id,
    content_hash,
    fetched_at,
    feed_header_ts,
    alert_id,
    entity_index,
    effect,
    cause,
    route_id,
    stop_id,
    active_start,
    active_end,
    header_text
from {{ source('curated', 'alerts') }}
{{ lookback_filter() }}
