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


def _evidence(line: str, cov: float, header: str | None = None) -> str:
    import json

    hours = [
        {"hour": "15:00", "date": "2026-11-22", "median_deviation_s": 420.0,
         "baseline_median_s": 40.0, "deviation_over_baseline_s": 380.0, "gap_share_pct": 35.0,
         "skipped_share_pct": 2.0, "coverage_pct": cov, "alert_active": header is not None,
         "crossed": ["deviation", "gap"]},
        {"hour": "16:00", "date": "2026-11-22", "median_deviation_s": 300.0,
         "baseline_median_s": 40.0, "deviation_over_baseline_s": 260.0, "gap_share_pct": 12.0,
         "skipped_share_pct": 1.0, "coverage_pct": 90.0, "alert_active": False,
         "crossed": ["deviation"]},
    ]  # fmt: skip
    alerts = [{"alert_id": "a1", "effect": "DETOUR", "header": header}] if header else []
    return json.dumps(
        {"line": line, "direction_id": 0, "start": "15:00", "end": "17:00", "n_hours": 2,
         "is_open": False, "hours": hours, "baseline_rule": "enabled", "baseline_days": 20,
         "data_outage_hours_inside": 0, "alerts": alerts}
    )  # fmt: skip


def incidents_table() -> pd.DataFrame:
    """Four incidents: LLM summary and matched alert (Red), template after a rejected text
    (Orange), no summary yet and unmatched (Blue), and a low-coverage one (Mattapan)."""
    base = {
        "evidence_hash": "h", "direction_id": 0, "n_hours": 2, "is_open": False,
        "thresholds_crossed": "deviation,gap", "mean_coverage_pct": 85.0,
        "n_outage_hours_inside": 0, "matched_alert_ids": None, "first_alert_id": None,
        "summary_generated_at": pd.Timestamp(T), "summary_evidence_hash": "h",
        "summary_stale": False, "rejection_reason": None, "llm_model": None,
        "incident_start_ts": pd.Timestamp("2026-11-22 20:00:00+00:00"),
        "incident_end_ts": pd.Timestamp("2026-11-22 22:00:00+00:00"),
    }  # fmt: skip
    rows = [
        {**base, "incident_id": "i1", "line": "Red", "min_coverage_pct": 88.0,
         "evidence_json": _evidence("Red", 88.0, "Shuttle buses replace trains"),
         "match_status": "matched", "alert_lag_s": 1200.0, "first_alert_id": "a1",
         "matched_alert_ids": "a1", "summary_text": "Red was late: deviation reached 420 s.",
         "template_used": False, "number_check": "passed", "llm_model": "m"},
        {**base, "incident_id": "i2", "line": "Orange", "min_coverage_pct": 75.0,
         "evidence_json": _evidence("Orange", 75.0), "match_status": "unmatched_incident",
         "alert_lag_s": None, "summary_text": "Orange (direction 0): template text.",
         "template_used": True, "number_check": "rejected",
         "rejection_reason": "number not in evidence: '99999 s'", "llm_model": "m"},
        {**base, "incident_id": "i3", "line": "Blue", "min_coverage_pct": 90.0,
         "evidence_json": _evidence("Blue", 90.0), "match_status": "unmatched_incident",
         "alert_lag_s": None, "summary_text": None, "template_used": None,
         "number_check": None, "summary_generated_at": None, "summary_evidence_hash": None,
         "summary_stale": None},
        {**base, "incident_id": "i4", "line": "Mattapan", "min_coverage_pct": 30.0,
         "evidence_json": _evidence("Mattapan", 30.0), "match_status": "unmatched_incident",
         "alert_lag_s": None, "summary_text": "Mattapan summary.", "template_used": True,
         "number_check": "not_run", "rejection_reason": "no_api_key"},
    ]  # fmt: skip
    return pd.DataFrame(rows)


def alert_matching_table() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "line": ["Red", "Orange"], "service_date": [pd.Timestamp("2026-11-22")] * 2,
            "n_incidents": [2, 2], "n_incidents_matched": [1, 0], "n_incidents_unmatched": [1, 2],
            "n_alerts": [3, 0], "n_alerts_matched": [1, 0], "n_alerts_unmatched": [2, 0],
            "median_alert_lag_s": [1200.0, None], "n_with_lag": [1, 0],
        }
    )  # fmt: skip


def with_incidents(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    gold = dest / "gold"
    incidents_table().to_parquet(gold / "agg_incidents.parquet")
    alert_matching_table().to_parquet(gold / "agg_alert_matching_daily.parquet")
    return dest
