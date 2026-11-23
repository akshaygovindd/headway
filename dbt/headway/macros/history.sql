{#
  Closed days from immutable history (ADR-015), open days from this build.

  `history_union(name)` ends a daily aggregate whose body is the CTE `computed`. When the
  history_root var is set (the transform Lambda with HEADWAY_ENABLE_R2=true) and history exists
  for the table's current metric version, it returns the computed rows of days that are not in
  history plus every history row; otherwise just the computed rows. The metric versions come in as
  the metric_versions var, built from headway_transform.history.METRIC_VERSIONS.
#}
{% macro history_glob(name) -%}
{{ var('history_root') }}/table={{ name }}/metric_version={{ var('metric_versions')[name] }}/service_date=*/part.parquet
{%- endmacro %}

{% macro history_union(name) -%}
{%- set active = var('history_root', none) is not none and name in var('metric_versions', {}) -%}
{%- if active and execute -%}
    {%- set glob = history_glob(name) -%}
    {%- set n = run_query("select count(*) from glob('" ~ glob ~ "')").columns[0].values()[0] -%}
{%- else -%}
    {%- set n = 0 -%}
{%- endif -%}
{%- if n > 0 -%}
select * from computed
where service_date not in (select distinct service_date from read_parquet('{{ glob }}'))
union all by name
select * from read_parquet('{{ glob }}')
{%- else -%}
select * from computed
{%- endif -%}
{%- endmacro %}
