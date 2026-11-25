{#
  Export the public gold tables as Parquet plus manifest.json.

  Usage: dbt run-operation export_gold --args '{target_dir: <dir>}'

  target_dir must already exist when it is a local path; an s3:// prefix works once
  the httpfs extension and credentials are available. Only the tables in the
  allowlist below are written; nothing else leaves the warehouse. The Release 2 aggregates
  (gold_r2_allowlist) are added only when HEADWAY_ENABLE_R2=true, the Release 3 forecast tables
  (gold_r3_allowlist: forecasts, incidents, alert matching, weather comparison) only with HEADWAY_ENABLE_R3=true as well. The Release 4 tables (gold_r4_allowlist: segment health,
  last-hour Marey arrivals, capture heartbeat) are added with HEADWAY_ENABLE_R4=true and R2, independent of R3.

  agg_pipeline_health is limited to the 30 days before its latest run.
  dbt_run_at is the start time of this dbt invocation (run the export right after
  `dbt build`). metrics_as_of is the start of the latest service hour in
  agg_line_reliability_hourly, in UTC.
#}
{% macro gold_r2_allowlist() -%}
{{ return({
    'agg_arrival_reconstruction_daily': "select * from " ~ ref('agg_arrival_reconstruction_daily'),
    'agg_prediction_accuracy_daily': "select * from " ~ ref('agg_prediction_accuracy_daily'),
    'agg_actual_deviation_hourly': "select * from " ~ ref('agg_actual_deviation_hourly'),
    'agg_headway_adherence_hourly': "select * from " ~ ref('agg_headway_adherence_hourly'),
    'agg_ett_daily': "select * from " ~ ref('agg_ett_daily'),
    'agg_active_alerts': "select * from " ~ ref('agg_active_alerts'),
    'agg_accuracy_window': "select * from " ~ ref('agg_accuracy_window'),
    'agg_actual_deviation_window': "select * from " ~ ref('agg_actual_deviation_window'),
    'agg_headway_window': "select * from " ~ ref('agg_headway_window'),
    'agg_ett_window': "select * from " ~ ref('agg_ett_window'),
    'agg_countdown_accuracy_window': "select * from " ~ ref('agg_countdown_accuracy_window'),
    'agg_countdown_accuracy_daily': "select * from " ~ ref('agg_countdown_accuracy_daily'),
    'agg_actual_deviation_daily': "select * from " ~ ref('agg_actual_deviation_daily'),
    'agg_headway_adherence_daily': "select * from " ~ ref('agg_headway_adherence_daily'),
    'agg_slo_daily': "select * from " ~ ref('agg_slo_daily'),
    'agg_slo_rollup': "select * from " ~ ref('agg_slo_rollup'),
}) }}
{%- endmacro %}

{#
  Release 3 forecast tables (HEADWAY_ENABLE_R3=true, with R2): the next-hour forecasts and the gate
  status always; the backtest summary only when it has rows, i.e. when at least one line has passed
  the training gate (agg_forecast_backtest keeps only gate-passed lines). A deployment whose gate
  never passed exports the gate status and no backtest.
#}
{% macro gold_r3_allowlist() -%}
{% set tables = {
    'agg_forecast_next_hour': "select * from " ~ ref('agg_forecast_next_hour'),
    'agg_forecast_gate_status': "select * from " ~ ref('agg_forecast_gate_status'),
    'agg_incidents': "select * from " ~ ref('agg_incidents'),
    'agg_alert_matching_daily': "select * from " ~ ref('agg_alert_matching_daily'),
    'agg_weather_comparison': "select * from " ~ ref('agg_weather_comparison'),
} %}
{% if execute %}
{% set n = run_query("select count(*) from " ~ ref('agg_forecast_backtest')).columns[0].values()[0] %}
{% if n > 0 %}
{% do tables.update({'agg_forecast_backtest': "select * from " ~ ref('agg_forecast_backtest')}) %}
{% endif %}
{% endif %}
{{ return(tables) }}
{%- endmacro %}

{#
  Release 4 tables (HEADWAY_ENABLE_R4=true, with R2) for the public site: segment health, the last
  complete hour of reconstructed arrivals (Marey chart) and the hourly capture heartbeat (last 30 days).
#}
{% macro gold_r4_allowlist() -%}
{{ return({
    'agg_headway_segment_window': "select * from " ~ ref('agg_headway_segment_window'),
    'agg_ett_band_window': "select * from " ~ ref('agg_ett_band_window'),
    'agg_service_disruptions': "select * from " ~ ref('agg_service_disruptions'),
    'agg_marey_last_hour': "select * from " ~ ref('agg_marey_last_hour'),
    'agg_capture_heartbeat_hourly': "select * from " ~ ref('agg_capture_heartbeat_hourly') ~ " where hour_start >= (select max(hour_start) from " ~ ref('agg_capture_heartbeat_hourly') ~ ") - interval 30 day",
}) }}
{%- endmacro %}

{% macro gold_allowlist() -%}
{% set tables = {
    'agg_line_reliability_hourly': "select * from " ~ ref('agg_line_reliability_hourly'),
    'agg_line_reliability_window': "select * from " ~ ref('agg_line_reliability_window'),
    'agg_stop_deviation_daily': "select * from " ~ ref('agg_stop_deviation_daily'),
    'agg_pipeline_health': "select * from " ~ ref('agg_pipeline_health') ~ " where scheduled_at >= (select max(scheduled_at) from " ~ ref('agg_pipeline_health') ~ ") - interval 30 day",
    'agg_quality_checks_hourly': "select * from " ~ ref('agg_quality_checks_hourly'),
    'agg_quarantine_reasons': "select * from " ~ ref('agg_quarantine_reasons'),
    'dim_route': "select * from " ~ ref('dim_route'),
    'dim_stop': "select * from " ~ ref('dim_stop'),
    'route_shapes': "select * from " ~ ref('route_shapes'),
} %}
{# Release 2 aggregates only exist when their models were built (HEADWAY_ENABLE_R2=true) #}
{% if env_var('HEADWAY_ENABLE_R2', 'false') | lower == 'true' %}
{% do tables.update(gold_r2_allowlist()) %}
{% if env_var('HEADWAY_ENABLE_R3', 'false') | lower == 'true' %}
{% do tables.update(gold_r3_allowlist()) %}
{% endif %}
{% if env_var('HEADWAY_ENABLE_R4', 'false') | lower == 'true' %}
{% do tables.update(gold_r4_allowlist()) %}
{% endif %}
{% endif %}
{{ return(tables) }}
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
