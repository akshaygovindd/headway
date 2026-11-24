-- Quarantined records per local date, source table and reason: every reason by name, however
-- many curation writes. Dated by the run's scheduled minute; records of captures that no
-- manifest knows (no run) are dated by their partition dt.
select
    coalesce(
        cast((m.scheduled_at at time zone '{{ var("timezone") }}') as date),
        q.quarantine_dt
    ) as local_date,
    q.source_table,
    q.reason,
    count(*) as n_records
from {{ ref('stg_quarantine') }} q
left join {{ ref('stg_manifests') }} m on m.capture_id = q.capture_id
group by 1, 2, 3
