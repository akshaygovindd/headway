-- Forecasts of the arrival delay for the next hour (Release 3, ADR-019), per line x direction x
-- stop x prediction time t x horizon (15, 30, 60 minutes): the mean over the trip stops scheduled
-- in the slot [t + horizon, t + horizon + 15 min), in seconds late (positive) or early.
--   persistence, daytype_hour, rolling_median_60m   the three baselines, as in the backtest
--   mbta_reference                                  MBTA's own predicted deviation: a reference,
--                                                   not a baseline
--   model_forecast_s                                the LightGBM forecast; null here, filled by
--                                                   the transform (headway_transform/
--                                                   forecast_serving.py) only for lines with an
--                                                   approved model and a passed gate
-- n_trips is the scheduled trip stops of the slot and n_<method> those that have a value, so
-- coverage is n_<method> / n_trips. feature_age_s is the time from the latest arrival the features
-- could use (known at t) to t. forecast_status says whether a model forecast exists and why not.
-- Exported with the R3 gold tables.
with f as (
    select * from {{ ref('int_forecast_serving_features') }}
),

typical as (
    select line, day_type, hour_local, avg(label_delay_s) as daytype_hour_pred
    from {{ ref('fct_forecast_examples') }}
    group by 1, 2, 3
),

known as (
    select g.line, g.prediction_ts, epoch(g.prediction_ts) - epoch(max(u.known_at)) as feature_age_s,
        max(u.known_at) as latest_arrival_known_at
    from {{ ref('int_forecast_serving_grid') }} g
    left join {{ ref('int_usable_arrivals') }} u
        on u.line = g.line and u.known_at <= g.prediction_ts
    group by 1, 2
)

select
    f.line,
    f.direction_id,
    f.stop_id,
    f.prediction_ts,
    f.horizon_min,
    count(*) as n_trips,
    cast(null as double) as model_forecast_s,
    0 as n_model,
    avg(f.last_upstream_delay_s) as persistence,
    count(f.last_upstream_delay_s) as n_persistence,
    avg(ty.daytype_hour_pred) as daytype_hour,
    count(ty.daytype_hour_pred) as n_daytype_hour,
    avg(f.rolling_median_delay_60m) as rolling_median_60m,
    count(f.rolling_median_delay_60m) as n_rolling_median_60m,
    avg(f.mbta_predicted_deviation_s) as mbta_reference,
    count(f.mbta_predicted_deviation_s) as n_mbta_reference,
    any_value(k.latest_arrival_known_at) as latest_arrival_known_at,
    any_value(k.feature_age_s) as feature_age_s,
    'baselines_only' as forecast_status,
    'no approved model for this line' as status_reason,
    cast(null as integer) as model_version
from f
left join typical ty on ty.line = f.line and ty.day_type = f.day_type and ty.hour_local = f.hour_local
left join known k on k.line = f.line and k.prediction_ts = f.prediction_ts
group by f.line, f.direction_id, f.stop_id, f.prediction_ts, f.horizon_min
