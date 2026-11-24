"""Synthetic Release 3 gold tables for the Forecast page tests: a gate that passed with the model
winning, losing or tying, next to a line below the gate and a line with too little coverage."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

T = datetime(2026, 11, 22, 15, 45, tzinfo=UTC)
METHODS = ["lightgbm", "persistence", "daytype_hour", "rolling_median_60m", "mbta_reference"]


def gate_table() -> pd.DataFrame:
    rows = [
        ("Red", 25, 6, True), ("Mattapan", 4, 1, False), ("Blue", 25, 6, True),
    ]  # fmt: skip
    return pd.DataFrame(
        {
            "line": [r[0] for r in rows], "n_days": [r[1] for r in rows],
            "n_weekend_days": [r[2] for r in rows], "min_days": 21, "min_weekend_days": 3,
            "gate_passed": [r[3] for r in rows], "source": "training_job",
            "updated_at": pd.Timestamp(T),
        }
    )  # fmt: skip


def next_hour_table() -> pd.DataFrame:
    rows = []
    for line, model_on, persistence_n in (
        ("Red", True, 4),
        ("Mattapan", False, 4),
        ("Blue", True, 1),
    ):
        for h in (15, 30, 60):
            for stop in ("a", "b"):
                rows.append(
                    {
                        "line": line, "direction_id": 0, "stop_id": stop, "prediction_ts": T,
                        "horizon_min": h, "n_trips": 4,
                        "model_forecast_s": 90.0 if model_on else None,
                        "n_model": 4 if model_on else 0,
                        "persistence": 60.0, "n_persistence": persistence_n,
                        "daytype_hour": 45.0, "n_daytype_hour": 4,
                        "rolling_median_60m": 30.0, "n_rolling_median_60m": 4,
                        "mbta_reference": 20.0, "n_mbta_reference": 4,
                        "latest_arrival_known_at": pd.Timestamp(T) - pd.Timedelta(minutes=2),
                        "feature_age_s": 120.0,
                        "forecast_status": "model" if model_on else "baselines_only",
                        "status_reason": "model v3" if model_on
                        else "training gate not passed",
                        "model_version": 3 if model_on else None,
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)


def backtest_table(state: str) -> pd.DataFrame:
    """state: win | lose | tie. Model MAE vs the best baseline (daytype_hour at 100 s)."""
    diff, lo, hi = {"win": (-30.0, -45.0, -15.0), "lose": (40.0, 20.0, 60.0),
                    "tie": (2.0, -10.0, 14.0)}[state]  # fmt: skip
    mae = {"lightgbm": 100.0 + diff, "persistence": 160.0, "daytype_hour": 100.0,
           "rolling_median_60m": 180.0, "mbta_reference": 120.0}  # fmt: skip
    rows = []
    for line in ("Red", "Blue"):
        for h in (15, 30, 60):
            for m in METHODS:
                model = m == "lightgbm"
                rows.append(
                    {
                        "line": line, "horizon_min": h, "period_type": "all", "view": "strict",
                        "method": m, "n": 900, "n_labelled": 1000, "coverage_pct": 90.0,
                        "mae": mae[m], "rmse": mae[m] * 1.3, "within_60": 40.0, "within_120": 70.0,
                        "ci_lo": mae[m] - 8, "ci_hi": mae[m] + 8, "fold_mae_min": mae[m] - 5,
                        "fold_mae_max": mae[m] + 5, "fold_mae_std": 3.0, "n_folds": 3,
                        "is_reference": m == "mbta_reference",
                        "best_baseline": "daytype_hour" if model else None,
                        "diff_mae": diff if model else None,
                        "diff_ci_lo": lo if model else None,
                        "diff_ci_hi": hi if model else None,
                        "run_id": "r1", "trained_at": pd.Timestamp(T),
                    }
                )  # fmt: skip
    return pd.DataFrame(rows)


def with_r3(src: Path, dest: Path, state: str | None) -> Path:
    """Copy an export and add the forecast tables (backtest only for win/lose/tie)."""
    shutil.copytree(src, dest)
    gold = dest / "gold"
    gate_table().to_parquet(gold / "agg_forecast_gate_status.parquet")
    next_hour_table().to_parquet(gold / "agg_forecast_next_hour.parquet")
    (gold / "agg_forecast_backtest.parquet").unlink(missing_ok=True)
    if state:
        backtest_table(state).to_parquet(gold / "agg_forecast_backtest.parquet")
    return dest
