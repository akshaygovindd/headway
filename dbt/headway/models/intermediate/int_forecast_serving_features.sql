-- Features of the trip stops to forecast now, one row per trip stop in the three slots. The SQL is
-- the training one (macros/forecast_features.sql, also used by fct_forecast_examples) over the
-- serving targets and line features, so a feature has the same definition in both; the label
-- columns are null. scripts/serving_parity.py checks the equality on historical times.
{{ forecast_example_features(ref('int_forecast_serving_targets'), ref('int_forecast_serving_line_features')) }}
