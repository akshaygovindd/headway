{{ config(severity='error') }}
-- Poll 0 of a run fetches every feed and ingest records an entry for each, ok or error, so a
-- delivered run has poll-0 entries of all three feeds. A run with fewer lost an entry between the
-- raw manifest and the curated table: vehicle polls 0 and 1, fetched within a second with the same
-- payload, share a capture_id, and curation once kept only the later poll, so the minute had no
-- vehicle poll 0 and read as missing capture. Rows are the runs that lost one; backfill_manifests
-- restores them from the raw manifests (DEPLOY.md). Duplicate-delivery records have no entries of
-- their own to count and are left out.
select
    run_id,
    min(scheduled_at) as scheduled_at,
    count(distinct feed) as feeds_at_poll_0,
    string_agg(distinct feed, ', ') as feeds
from {{ ref('stg_manifest_entries') }}
where poll_index = 0 and status <> 'duplicate_delivery'
group by run_id
having count(distinct feed) <> 3
