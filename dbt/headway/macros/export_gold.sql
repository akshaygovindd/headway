{#
  Export the public gold tables as Parquet plus manifest.json.

  Usage: dbt run-operation export_gold --args '{target_dir: <dir>}'

  target_dir must already exist when it is a local path; an s3:// prefix works once
  the httpfs extension and credentials are available. Only the tables in the
  allowlist below are written; nothing else leaves the warehouse.

  agg_pipeline_health is limited to the 30 days before its latest run.
  dbt_run_at is the start time of this dbt invocation (run the export right after
  `dbt build`). metrics_as_of is the start of the latest service hour in
  agg_line_reliability_hourly, in UTC.
#}
{% macro gold_allowlist() -%}
{{ return({
    'agg_line_reliability_hourly': "select * from " ~ ref('agg_line_reliability_hourly'),
    'agg_stop_deviation_daily': "select * from " ~ ref('agg_stop_deviation_daily'),
    'agg_pipeline_health': "select * from " ~ ref('agg_pipeline_health') ~ " where scheduled_at >= (select max(scheduled_at) from " ~ ref('agg_pipeline_health') ~ ") - interval 30 day",
    'agg_quality_checks_hourly': "select * from " ~ ref('agg_quality_checks_hourly'),
    'dim_route': "select * from " ~ ref('dim_route'),
    'dim_stop': "select * from " ~ ref('dim_stop'),
    'route_shapes': "select * from " ~ ref('route_shapes'),
}) }}
{%- endmacro %}

{% macro export_gold(target_dir) %}
    {% if not execute %}{{ return('') }}{% endif %}
    {% set dir = target_dir | trim %}
    {% set dir = dir[:-1] if dir.endswith('/') else dir %}
    {% set tables = gold_allowlist() %}
    {% set counts = {} %}
    {% for name, query in tables.items() %}
        {% do run_query("copy (" ~ query ~ ") to '" ~ dir ~ "/" ~ name ~ ".parquet' (format parquet)") %}
        {% set n = run_query("select count(*) from read_parquet('" ~ dir ~ "/" ~ name ~ ".parquet')").columns[0].values()[0] %}
        {% do counts.update({name: n}) %}
    {% endfor %}

    {% set cutoff = var('service_day_cutoff_hour') %}
    {% set tz = var('timezone') %}
    {% set as_of_sql %}
        select strftime(
            max(
                (cast(service_date as timestamp)
                    + to_hours(hour_local + case when hour_local < {{ cutoff }} then 24 else 0 end))
                at time zone '{{ tz }}'
            ) at time zone 'UTC',
            '%Y-%m-%dT%H:%M:%SZ'
        ) from {{ ref('agg_line_reliability_hourly') }}
    {% endset %}
    {% set as_of = run_query(as_of_sql).columns[0].values()[0] %}

    {% set pairs = [] %}
    {% for name, n in counts.items() %}
        {% do pairs.append("'" ~ name ~ "': " ~ n) %}
    {% endfor %}
    {% set manifest_sql %}
        select
            '{{ modules.datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ") }}' as exported_at,
            '{{ run_started_at.astimezone(modules.pytz.utc).strftime("%Y-%m-%dT%H:%M:%SZ") }}' as dbt_run_at,
            {% if as_of is none %}null::varchar{% else %}'{{ as_of }}'{% endif %} as metrics_as_of,
            {{ '{' ~ pairs | join(', ') ~ '}' }} as row_counts
    {% endset %}
    {% do run_query("copy (" ~ manifest_sql ~ ") to '" ~ dir ~ "/manifest.json' (format json, array false)") %}
    {{ log("exported " ~ counts | length ~ " gold tables to " ~ dir, info=true) }}
{% endmacro %}
