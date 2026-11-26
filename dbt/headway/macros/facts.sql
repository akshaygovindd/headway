{#
  Closed-day facts (ADR-024): the heavy facts of a service day that has closed are computed once,
  persisted as immutable Parquet, and read back on later runs.

  The transform sets three vars when it has persisted days to load:
    compute_days   service dates (ISO) with no persisted partition: open days AND closed days not
                   yet persisted. Null computes every day (local builds, replay, kill switch).
    persisted_days service dates whose partition is read from state instead of being computed.
    facts_root / fact_versions   where the partitions are and the version of each table.
  compute_days and persisted_days partition the window, so the union is exactly a full build.
#}
{% macro compute_days_filter(col) -%}
{%- set days = var('compute_days', none) -%}
{%- if days is none -%}
true
{%- elif days | length == 0 -%}
false
{%- else -%}
{{ col }} in ({% for d in days %}date '{{ d }}'{{ ', ' if not loop.last }}{% endfor %})
{%- endif -%}
{%- endmacro %}

{#
  Ends a fact whose body is the CTE `computed`: the computed days plus the persisted days.
#}
{% macro fact_union(name) -%}
{%- set persisted = var('persisted_days', []) -%}
{%- if var('facts_root', none) is not none and persisted | length > 0 and name in var('fact_versions', {}) -%}
select * from computed
union all by name
select * from read_parquet([
    {% for d in persisted -%}
    '{{ var("facts_root") }}/{{ name }}/metric_version={{ var("fact_versions")[name] }}/service_date={{ d }}/part.parquet'{{ ',' if not loop.last }}
    {% endfor -%}
], hive_partitioning = false)
{%- else -%}
select * from computed
{%- endif -%}
{%- endmacro %}
