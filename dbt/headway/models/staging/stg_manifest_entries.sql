{#- Parts written before 15 s sampling have no poll_index column; when every part is that old the
    column does not exist at all, so look before selecting it. -#}
{% set manifest_columns = run_query('select * from ' ~ source('curated', 'manifests') ~ ' limit 0').column_names if execute else [] %}
-- The curated manifests table as written by curation, with the lookback filter and a poll_index
-- that is never null (0 for parts written before 15 s sampling). Rows are not deduplicated here:
-- stg_manifest_deliveries does that.
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
