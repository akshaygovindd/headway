-- Training gate per line as the Forecast page shows it (Release 3, ADR-019). The training job's
-- table (all closed service days, raw_forecast_gate_status) wins per line; a line it has not seen
-- falls back to the build's agg_forecast_gate, which counts the lookback window only. `source`
-- says which. Exported with the R3 gold tables.
{% set raw = forecast_report_relation('raw_forecast_gate_status') %}
with job as (
    {% if raw %}
    select
        line, cast(n_days as bigint) as n_days, cast(n_weekend_days as bigint) as n_weekend_days,
        cast(min_days as bigint) as min_days, cast(min_weekend_days as bigint) as min_weekend_days,
        cast(gate_passed as boolean) as gate_passed,
        cast(trained_at as timestamp with time zone) as updated_at
    from {{ raw }}
    {% else %}
    select
        cast(null as varchar) as line, cast(null as bigint) as n_days, cast(null as bigint) as n_weekend_days,
        cast(null as bigint) as min_days, cast(null as bigint) as min_weekend_days,
        cast(null as boolean) as gate_passed, cast(null as timestamp with time zone) as updated_at
    where false
    {% endif %}
)

select line, n_days, n_weekend_days, min_days, min_weekend_days, gate_passed,
    'training_job' as source, updated_at
from job
union all
select
    line, cast(n_days as bigint), cast(n_weekend_days as bigint), cast(min_days as bigint),
    cast(min_weekend_days as bigint), gate_passed, 'transform_build' as source,
    cast(null as timestamp with time zone) as updated_at
from {{ ref('agg_forecast_gate') }}
where line not in (select line from job)
