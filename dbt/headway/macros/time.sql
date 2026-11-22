{#
  GTFS time to UTC. Per the GTFS rule, times are measured from "noon minus 12h"
  on the service date: local noon on service_date, minus 12 hours of absolute
  time, plus the HH:MM:SS offset (hours may exceed 24).
#}
{% macro gtfs_time_to_utc(service_date, hhmmss) -%}
(
    (cast({{ service_date }} as date) + interval 12 hour) at time zone '{{ var("timezone") }}'
    - interval 12 hour
    + to_seconds(
        cast(split_part({{ hhmmss }}, ':', 1) as bigint) * 3600
        + cast(split_part({{ hhmmss }}, ':', 2) as bigint) * 60
        + cast(split_part({{ hhmmss }}, ':', 3) as bigint)
    )
)
{%- endmacro %}

{#
  Service date of a timestamp: the local date, minus one day before the cutoff hour.
#}
{% macro service_date_from_ts(ts) -%}
cast(({{ ts }} at time zone '{{ var("timezone") }}') - interval {{ var("service_day_cutoff_hour") }} hour as date)
{%- endmacro %}

{#
  Rolling window over the hive `dt` partition column of a realtime source. Emits nothing
  unless the lookback_days var is set, so local and replay builds read everything.
  Used only in staging models of realtime sources (never static tables).
#}
{% macro lookback_filter() -%}
{%- if var('lookback_days', none) is not none -%}
where cast(dt as date) >= cast(now() at time zone 'UTC' as date) - {{ var('lookback_days') | int }}
{%- endif -%}
{%- endmacro %}
