-- Line-level features at the serving prediction time. The SQL is the training one
-- (macros/forecast_features.sql, also used by int_forecast_line_features), over the serving grid.
{{ forecast_line_features(ref('int_forecast_serving_grid')) }}
