-- One row per quarantined record (or whole capture for parse errors), joined to
-- its run through the capture manifest.
with q as (
    select
        capture_id,
        "table" as source_table,
        reason,
        split_part(error, chr(10), 1) as error,
        dt
    from {{ source('curated', 'quarantine') }}
    {{ lookback_filter() }}
)

select
    q.capture_id,
    m.run_id,
    q.source_table,
    q.reason,
    q.error,
    q.dt as quarantine_dt,
    row_number() over (
        partition by q.capture_id, q.source_table
        order by q.reason, q.error
    ) as quarantine_seq
from q
left join {{ ref('stg_manifests') }} m on m.capture_id = q.capture_id
