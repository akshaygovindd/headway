{#
  The tables the training job publishes (models/forecast/reports/*.parquet) are loaded into the
  warehouse by the transform before the build (headway_transform/forecast_serving.py), as
  raw_forecast_backtest and raw_forecast_gate_status. Builds that have not loaded them (local,
  e2e, a deployment whose training job has not run yet) read an empty, typed table instead.
#}
{% macro forecast_report_relation(name) %}
    {%- if execute -%}
        {{ return(adapter.get_relation(database=target.database, schema=target.schema, identifier=name)) }}
    {%- endif -%}
    {{ return(none) }}
{% endmacro %}
