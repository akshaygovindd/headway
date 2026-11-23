{#- Parts written before 15 s sampling have no poll_index column; when every part is that old the
    column does not exist at all, so look before selecting it. -#}
{% set manifest_columns = run_query('select * from ' ~ source('curated', 'manifests') ~ ' limit 0').column_names if execute else [] %}
-- Every delivery row in the curated manifests table: one per capture attempt per delivery of a
-- scheduled minute. EventBridge Scheduler is at-least-once, so a minute can be delivered twice.
--
-- delivery_rank is the dedupe rule, applied to the rows ingest recorded as normal manifests:
-- among rows sharing a capture_id (same feed, same fetch second, same payload hash) the earliest
-- fetched_at wins, ties broken by the lowest run_id; the winner has rank 1. Rows ingest recorded
-- as duplicate deliveries (status = 'duplicate_delivery') have no rank: they are never kept.
-- stg_manifests keeps rank 1; agg_pipeline_health counts the rest.
with deliveries as (
    select
        capture_id,
        run_id,
        city_id,
        feed,
        scheduled_at,
        {% if 'poll_index' in manifest_columns %}coalesce(poll_index, 0){% else %}0{% endif %} as poll_index,
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
    -- one row per (run, capture); collapses parts written twice by overlapping curation
    qualify row_number() over (partition by run_id, capture_id order by fetched_at) = 1
)

select
    *,
    date_trunc('minute', scheduled_at) as scheduled_minute,
    case
        when status = 'duplicate_delivery' then null
        else row_number() over (
            partition by capture_id
            order by (status = 'duplicate_delivery'), fetched_at, run_id
        )
    end as delivery_rank
from deliveries
