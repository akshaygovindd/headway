-- Hourly actual deviation for the incident baseline (Release 3): this build's agg_actual_deviation_hourly
-- plus the closed days of immutable history (ADR-015), through history_union. The lookback window
-- holds a few days; the day type x hour baseline needs weeks, which history supplies.
with computed as (
    select * from {{ ref('agg_actual_deviation_hourly') }}
)

{{ history_union('agg_actual_deviation_hourly') }}
