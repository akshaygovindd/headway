-- Persisted transform runs (agg_transform_runs): one row per invocation of the transform Lambda,
-- loaded from s3://<raw>/state/transform_runs/ into the local database before dbt runs (see
-- headway_transform.state). Builds without it (local, replay, e2e) get an empty table of the same
-- shape. The run that is building right now is not in it yet.
{% set persisted = adapter.get_relation(database=target.database, schema=target.schema, identifier='agg_transform_runs') %}
{% if persisted is not none %}
select
    run_id,
    task,
    slot,
    started_at,
    finished_at,
    status,
    tests_passed,
    tests_failed,
    rows_exported,
    gold_exported_at,
    gold_published,
    r2_enabled,
    duration_ms,
    step_ms,
    error
from {{ persisted }}
{% else %}
select
    cast(null as varchar) as run_id,
    cast(null as varchar) as task,
    cast(null as timestamptz) as slot,
    cast(null as timestamptz) as started_at,
    cast(null as timestamptz) as finished_at,
    cast(null as varchar) as status,
    cast(null as integer) as tests_passed,
    cast(null as integer) as tests_failed,
    cast(null as bigint) as rows_exported,
    cast(null as timestamptz) as gold_exported_at,
    cast(null as boolean) as gold_published,
    cast(null as boolean) as r2_enabled,
    cast(null as bigint) as duration_ms,
    cast(null as varchar) as step_ms,
    cast(null as varchar) as error
where false
{% endif %}
