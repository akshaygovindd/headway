-- One row per capture attempt recorded in an ingest run manifest (grain: capture_id).
--
-- Dedupe rule: when two runs recorded the same capture_id (a second scheduler delivery of the
-- same minute that fetched within the same second), the row of the earliest fetched_at is kept,
-- ties broken by the lowest run_id (delivery_rank = 1 in stg_manifest_deliveries). Rows ingest
-- flagged as duplicate deliveries are excluded. The dropped deliveries are counted, not lost:
-- see agg_pipeline_health.duplicate_delivery_count.
select
    capture_id,
    run_id,
    city_id,
    feed,
    scheduled_at,
    poll_index,
    n_polls,
    fetched_at,
    status,
    http_status,
    bytes,
    entity_count,
    content_hash,
    feed_header_ts,
    header_parse_error,
    error
from {{ ref('stg_manifest_deliveries') }}
where delivery_rank = 1
