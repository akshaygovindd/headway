"""Frames and wording of the Forecast page (Release 3, ADR-019).

The tables hold per slot values and the counts behind them; this module combines slots by weighting
each with its count, derives coverage from the counts, and decides what the comparison banner may
say. The withholding rule of every page applies: no value from under 50% coverage.
"""

from dataclasses import dataclass

import pandas as pd

from lib.coverage import is_withheld

MODEL = "model"
BASELINES = ("persistence", "daytype_hour", "rolling_median_60m")
REFERENCE = "mbta_reference"
LABELS = {
    MODEL: "Model (LightGBM)",
    "lightgbm": "Model (LightGBM)",
    "persistence": "Persistence (baseline)",
    "daytype_hour": "Typical for day type and hour (baseline)",
    "rolling_median_60m": "Line's last 60 minutes (baseline)",
    REFERENCE: "MBTA prediction (reference, not a baseline)",
}
HORIZONS = (15, 30, 60)
GATE_TEXT = (
    "comparison unlocks after {days} service days incl. {weekend} weekends; "
    "currently {n} days ({w} weekend)"
)


def gate_line(row: pd.Series) -> str:
    """The gate of one line in words."""
    n, w = int(row["n_days"]), int(row["n_weekend_days"])
    days, weekend = int(row["min_days"]), int(row["min_weekend_days"])
    if bool(row["gate_passed"]):
        return f"gate passed: {n} service days ({w} weekend days)"
    return GATE_TEXT.format(days=days, weekend=weekend, n=n, w=w)


def next_hour_table(nh: pd.DataFrame, line: str) -> pd.DataFrame:
    """One row per horizon and method for the line: the forecast (mean of the slots of all stops,
    weighted by the trip stops that have a value), its coverage and n. The model row is present
    only when the line has a model forecast; a baseline without any value keeps its row (n = 0)."""
    sel = nh[nh["line"] == line]
    rows = []
    model_on = bool((sel["forecast_status"] == "model").any())
    for h in HORIZONS:
        part = sel[sel["horizon_min"] == h]
        if part.empty:
            continue
        total = float(part["n_trips"].sum())
        methods = [(MODEL, "model_forecast_s", "n_model")] if model_on else []
        methods += [
            ("persistence", "persistence", "n_persistence"),
            ("daytype_hour", "daytype_hour", "n_daytype_hour"),
            ("rolling_median_60m", "rolling_median_60m", "n_rolling_median_60m"),
            (REFERENCE, "mbta_reference", "n_mbta_reference"),
        ]
        for method, value, count in methods:
            n = float(part[count].sum())
            have = part[part[count] > 0]
            mean = (
                float((have[value] * have[count]).sum() / have[count].sum()) if len(have) else None
            )
            rows.append(
                {
                    "horizon_min": h, "method": method, "value": mean, "n": int(n),
                    "coverage_pct": 100.0 * n / total if total else None,
                }
            )  # fmt: skip
    return pd.DataFrame(rows, columns=["horizon_min", "method", "value", "n", "coverage_pct"])


def line_status(nh: pd.DataFrame, line: str) -> tuple[str, str]:
    """(forecast_status, reason) of the line: model, or baselines_only with the reason."""
    sel = nh[nh["line"] == line]
    if sel.empty:
        return "baselines_only", "no scheduled service in the next hour"
    if (sel["forecast_status"] == "model").any():
        return "model", str(sel.loc[sel["forecast_status"] == "model", "status_reason"].iloc[0])
    return "baselines_only", str(sel["status_reason"].iloc[0])


def feature_age_s(nh: pd.DataFrame, line: str) -> float | None:
    """Seconds from the latest arrival the features could use to the prediction time."""
    ages = nh.loc[nh["line"] == line, "feature_age_s"].dropna()
    return float(ages.min()) if len(ages) else None


@dataclass(frozen=True)
class Comparison:
    horizon_min: int
    verdict: str  # better | worse | none | withheld
    text: str


def _interval(lo: float, hi: float) -> str:
    return f"[{lo:+.0f}, {hi:+.0f}] s"


def comparison(bt: pd.DataFrame | None, line: str, horizon: int) -> Comparison | None:
    """Model against its best baseline on the strict view, from the paired bootstrap of the MAE
    difference (model minus best baseline). None when the backtest has no model row for the line."""
    if bt is None or bt.empty:
        return None
    base = bt[
        (bt["line"] == line) & (bt["horizon_min"] == horizon) & (bt["period_type"] == "all")
        & (bt["view"] == "strict")
    ]  # fmt: skip
    mdl = base[base["method"] == "lightgbm"]
    if mdl.empty:
        return None
    r = mdl.iloc[0]
    best = str(r["best_baseline"])
    peer = base[base["method"] == best]
    cov = min(float(r["coverage_pct"]), float(peer["coverage_pct"].iloc[0]) if len(peer) else 0.0)
    label = LABELS.get(best, best)
    if is_withheld(cov) or pd.isna(r["diff_mae"]):
        return Comparison(horizon, "withheld", "withheld (<50% coverage)")
    d, lo, hi = float(r["diff_mae"]), float(r["diff_ci_lo"]), float(r["diff_ci_hi"])
    if hi < 0:
        return Comparison(
            horizon, "better",
            f"model better by {-d:.0f} s {_interval(-hi, -lo)} than {label} (mean absolute error)",
        )  # fmt: skip
    if lo > 0:
        return Comparison(
            horizon, "worse",
            f"model worse by {d:.0f} s {_interval(lo, hi)} than {label} (mean absolute error)",
        )  # fmt: skip
    return Comparison(
        horizon, "none",
        f"no significant difference from {label}: {d:+.0f} s {_interval(lo, hi)} "
        "(model minus baseline, mean absolute error)",
    )  # fmt: skip


def context_table(bt: pd.DataFrame | None, line: str) -> pd.DataFrame:
    """Per-method MAE with its own 95% interval, pooled strict view, for the line."""
    cols = ["horizon_min", "method", "mae", "ci_lo", "ci_hi", "n", "coverage_pct"]
    if bt is None or bt.empty:
        return pd.DataFrame(columns=cols)
    sel = bt[(bt["line"] == line) & (bt["period_type"] == "all") & (bt["view"] == "strict")]
    return sel[cols].sort_values(["horizon_min", "method"]).reset_index(drop=True)
