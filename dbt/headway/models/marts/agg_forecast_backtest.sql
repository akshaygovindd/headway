-- Backtest summary of the forecast model against the baselines (Release 3, ADR-019), pooled over
-- the weekly folds: per method x line x horizon x period_type x view, with the 95% cluster
-- bootstrap interval of the MAE, the spread of the per-fold MAE and, on the strict view's model
-- rows, the paired difference to the best baseline. Only lines past the training gate are kept,
-- so a line below the gate never has a result here. The table is exported only when it has rows
-- (macros/export_gold.sql). The rows come from the training job (raw_forecast_backtest).
{% set raw = forecast_report_relation('raw_forecast_backtest') %}
{% if raw %}
select
    b.line, cast(b.horizon_min as integer) as horizon_min, b.period_type, b."view", b.method,
    cast(b.n as bigint) as n, cast(b.n_labelled as bigint) as n_labelled,
    100.0 * b.n / nullif(b.n_labelled, 0) as coverage_pct,
    b.mae, b.rmse, b.within_60, b.within_120, b.ci_lo, b.ci_hi,
    b.fold_mae_min, b.fold_mae_max, b.fold_mae_std, cast(b.n_folds as integer) as n_folds,
    cast(b.is_reference as boolean) as is_reference,
    b.best_baseline, b.diff_mae, b.diff_ci_lo, b.diff_ci_hi,
    b.run_id, cast(b.trained_at as timestamp with time zone) as trained_at
from {{ raw }} b
where b.line in (select line from {{ ref('agg_forecast_gate_status') }} where gate_passed)
{% else %}
select
    cast(null as varchar) as line, cast(null as integer) as horizon_min, cast(null as varchar) as period_type,
    cast(null as varchar) as "view", cast(null as varchar) as method, cast(null as bigint) as n,
    cast(null as bigint) as n_labelled, cast(null as double) as coverage_pct, cast(null as double) as mae,
    cast(null as double) as rmse, cast(null as double) as within_60, cast(null as double) as within_120,
    cast(null as double) as ci_lo, cast(null as double) as ci_hi, cast(null as double) as fold_mae_min,
    cast(null as double) as fold_mae_max, cast(null as double) as fold_mae_std, cast(null as integer) as n_folds,
    cast(null as boolean) as is_reference, cast(null as varchar) as best_baseline, cast(null as double) as diff_mae,
    cast(null as double) as diff_ci_lo, cast(null as double) as diff_ci_hi, cast(null as varchar) as run_id,
    cast(null as timestamp with time zone) as trained_at
where false
{% endif %}
