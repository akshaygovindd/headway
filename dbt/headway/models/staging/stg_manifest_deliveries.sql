-- Every delivery row in the curated manifests table: one per capture attempt per delivery of a
-- scheduled minute. EventBridge Scheduler is at-least-once, so a minute can be delivered twice.
--
-- n_polls is how many polls of the run share the capture_id (1, or 2 when vehicle polls 0 and 1
-- returned the same payload within a second).
--
-- delivery_rank is the dedupe rule, applied to the rows ingest recorded as normal manifests:
-- among rows sharing a capture_id (same feed, same fetch second, same payload hash) the earliest
-- fetched_at wins, ties broken by the lowest run_id; the winner has rank 1. Rows ingest recorded
-- as duplicate deliveries (status = 'duplicate_delivery') have no rank: they are never kept.
-- stg_manifests keeps rank 1; agg_pipeline_health counts the rest.
with polls as (
    select *
    from {{ ref('stg_manifest_entries') }}
    -- one row per (run, capture, poll); collapses parts written twice by overlapping curation
    qualify row_number() over (partition by run_id, capture_id, poll_index order by fetched_at) = 1
),

-- Vehicle polls 0 and 1 of a minute are fetched a fraction of a second apart and, with an
-- unchanged payload, share a capture_id (feed, fetch second, payload hash). They are one capture
-- of one payload: the earliest poll represents it (poll 0 for the minute's status) and n_polls
-- says how many polls it stands for, so polls are still counted exactly.
deliveries as (
    select
        *,
        count(*) over (partition by run_id, capture_id) as n_polls
    from polls
    qualify row_number() over (partition by run_id, capture_id order by fetched_at, poll_index) = 1
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
