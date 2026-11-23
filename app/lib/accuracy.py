"""Frames for the Release 2 pages: accuracy, actual deviation, headway adherence, ETT.

The marts hold the per-day or per-hour statistics; this module combines rows by weighting each
with the count behind it (n). Medians of medians are approximations, so the pages say so. Every
frame carries coverage_pct (numerator and denominator summed, never averaged).
"""

import pandas as pd

from lib.coverage import LOW_MIN

WINDOWS = {"last_24h": "Last 24 hours", "last_7d": "Last 7 days"}
ALL_DAYS = "all_days"
BUCKETS = ["0-5", "5-10", "10-20", "20-30"]
HEAVY_RAIL = ("Red", "Orange", "Blue")
TIME_BANDS = ["am_peak", "midday", "pm_peak", "evening"]
BAND_LABELS = {"am_peak": "AM peak", "midday": "Midday", "pm_peak": "PM peak", "evening": "Evening"}


def is_heavy_rail(line: str) -> bool:
    return line in HEAVY_RAIL


def _wmean(values: pd.Series, weights: pd.Series) -> float | None:
    mask = values.notna() & (weights > 0)
    if not mask.any():
        return None
    return float((values[mask] * weights[mask]).sum() / weights[mask].sum())


def _cov(num: pd.Series, den: pd.Series) -> float | None:
    d = float(den.sum())
    return 100.0 * float(num.sum()) / d if d else None


def accuracy_by_horizon(acc: pd.DataFrame, line: str | None = None) -> pd.DataFrame:
    """One row per line and horizon bucket over all days; disruption hours kept (see column).

    median_abs_error_s is the n-weighted mean of the daily medians. mae and the within shares are
    exact. n_disruption counts errors from service-disruption hours.
    """
    cols = ["line", "horizon_bucket", "n", "median_abs_error_s", "mae_s", "pct_within_60s",
            "pct_within_120s", "n_disruption", "coverage_pct"]  # fmt: skip
    sel = acc if line is None else acc[acc["line"] == line]
    rows = []
    for (ln, bucket), g in sel.groupby(["line", "horizon_bucket"]):
        w = g["n"].astype(float)
        rows.append(
            {
                "line": ln,
                "horizon_bucket": bucket,
                "n": int(w.sum()),
                "median_abs_error_s": _wmean(g["median_abs_error_s"], w),
                "mae_s": _wmean(g["mae_s"], w),
                "pct_within_60s": _wmean(g["pct_within_60s"], w),
                "pct_within_120s": _wmean(g["pct_within_120s"], w),
                "n_disruption": int(g.loc[g["service_disruption"], "n"].sum()),
                "coverage_pct": _cov(g["coverage_numerator"], g["coverage_denominator"]),
            }
        )
    out = pd.DataFrame(rows, columns=cols)
    if out.empty:
        return out
    out["horizon_bucket"] = pd.Categorical(out["horizon_bucket"], BUCKETS, ordered=True)
    return out.sort_values(["line", "horizon_bucket"]).reset_index(drop=True)


def window_options(*tables: pd.DataFrame | None) -> dict[str, str]:
    """Window choices for the pages: the exact windows when every window table was exported, and
    always all exported days (combined from daily or hourly rows, so approximate)."""
    exact = all(t is not None and not t.empty for t in tables)
    return {**(WINDOWS if exact else {}), ALL_DAYS: "All exported days (approximate)"}


def accuracy_window_by_horizon(win: pd.DataFrame, window_label: str) -> pd.DataFrame:
    """One row per line and horizon bucket from agg_accuracy_window: exact, nothing combined."""
    cols = ["line", "horizon_bucket", "n", "median_abs_error_s", "mae_s", "pct_within_60s",
            "pct_within_120s", "n_disruption", "coverage_pct"]  # fmt: skip
    out = win[win["window_label"] == window_label][cols].copy()
    if out.empty:
        return out
    out["horizon_bucket"] = pd.Categorical(out["horizon_bucket"], BUCKETS, ordered=True)
    return out.sort_values(["line", "horizon_bucket"]).reset_index(drop=True)


def deviation_window_summary(win: pd.DataFrame, window_label: str) -> pd.DataFrame:
    """One row per line from agg_actual_deviation_window: exact median, p90 and on-time share."""
    sel = win[win["window_label"] == window_label]
    return pd.DataFrame(
        {
            "line": sel["line"],
            "n": sel["n_arrivals"].astype(int),
            "median_deviation_s": sel["median_deviation_s"],
            "p90_deviation_s": sel["p90_deviation_s"],
            "pct_on_time_window": sel["pct_on_time_window"],
            "n_excluded_outage": sel["n_excluded_outage"].astype(int),
            "n_disruption": sel["n_disruption"].astype(int),
            "coverage_pct": sel["coverage_pct"],
        }
    ).reset_index(drop=True)


def headway_window_summary(
    win: pd.DataFrame, line: str, window_label: str
) -> dict[str, float | None]:
    """Same keys as headway_summary, from agg_headway_window (all directions of the line)."""
    sel = win[(win["line"] == line) & (win["window_label"] == window_label)]
    empty = {
        k: None
        for k in ("pct_within_band", "pct_bunched", "pct_gap", "median_ratio", "coverage_pct")
    }
    if sel.empty:
        return {"n": 0.0, **empty, "n_dropped": 0.0, "n_excluded_outage": 0.0, "n_disruption": 0.0}
    r = sel.iloc[0]
    dropped = sum(
        int(r[c])
        for c in (
            "n_dropped_train_between",
            "n_dropped_unproven_order",
            "n_dropped_no_scheduled_headway",
            "n_dropped_implausible",
        )
    )
    return {
        "n": float(r["n_headways"]),
        "pct_within_band": _num(r["pct_within_band"]),
        "pct_bunched": _num(r["pct_bunched"]),
        "pct_gap": _num(r["pct_gap"]),
        "median_ratio": _num(r["median_ratio"]),
        "n_dropped": float(dropped),
        "n_excluded_outage": float(r["n_excluded_outage"]),
        "n_disruption": float(r["n_disruption"]),
        "coverage_pct": _num(r["coverage_pct"]),
    }


def _num(value: object) -> float | None:
    return None if value is None or pd.isna(value) else float(value)  # type: ignore[arg-type]


def ett_window_row(win: pd.DataFrame, line: str, window_label: str) -> dict[str, float | None]:
    """Exact ETT of the line over the window (all directions, bands and day types)."""
    sel = win[(win["line"] == line) & (win["window_label"] == window_label)]
    if sel.empty:
        return {"n": 0.0, "median_ett_s": None, "p90_ett_s": None, "coverage_pct": None}
    r = sel.iloc[0]
    return {
        "n": float(r["n_trips"]),
        "median_ett_s": _num(r["median_ett_s"]),
        "p90_ett_s": _num(r["p90_ett_s"]),
        "coverage_pct": _num(r["coverage_pct"]),
    }


def _select(df: pd.DataFrame, line: str, direction: int | None) -> pd.DataFrame:
    out = df[df["line"] == line]
    return out if direction is None else out[out["direction_id"] == direction]


def actual_deviation_summary(dev: pd.DataFrame) -> pd.DataFrame:
    """One row per line: median, p90 (n-weighted over usable hours), share in the -1/+5 window."""
    cols = ["line", "n", "median_deviation_s", "p90_deviation_s", "pct_on_time_window",
            "n_excluded_outage", "n_disruption_hours", "coverage_pct"]  # fmt: skip
    rows = []
    for line, g in dev.groupby("line"):
        usable = g[~g["data_outage"]]
        w = usable["n_arrivals"].astype(float)
        rows.append(
            {
                "line": line,
                "n": int(w.sum()),
                "median_deviation_s": _wmean(usable["median_deviation_s"], w),
                "p90_deviation_s": _wmean(usable["p90_deviation_s"], w),
                "pct_on_time_window": _wmean(usable["pct_on_time_window"], w),
                "n_excluded_outage": int(g["n_excluded_outage"].sum()),
                "n_disruption_hours": int(usable["service_disruption"].sum()),
                "coverage_pct": _cov(usable["coverage_numerator"], usable["coverage_denominator"]),
            }
        )
    return pd.DataFrame(rows, columns=cols)


def arrival_coverage(recon: pd.DataFrame) -> pd.DataFrame:
    """Share of reconstructed trip stops with high or medium confidence, per line."""
    g = recon.groupby("line")[["trip_stops", "n_high", "n_medium"]].sum()
    out = g.reset_index()
    out["usable_pct"] = (
        100.0 * (out["n_high"] + out["n_medium"]) / out["trip_stops"].where(out["trip_stops"] > 0)
    )
    return out[["line", "trip_stops", "usable_pct"]]


def arrival_coverage_note(recon: pd.DataFrame | None, line: str | None = None) -> str:
    """One-line note on how much of the reconstruction the numbers rest on."""
    if recon is None or recon.empty:
        return "Arrival coverage: n/a."
    sel = recon if line is None else recon[recon["line"] == line]
    if sel.empty or sel["trip_stops"].sum() == 0:
        return "Arrival coverage: n/a."
    usable = 100.0 * (sel["n_high"].sum() + sel["n_medium"].sum()) / sel["trip_stops"].sum()
    return (
        f"Arrival coverage: {usable:.0f}% of {int(sel['trip_stops'].sum()):,} reconstructed trip "
        "stops have high or medium confidence; only those are used. Arrivals are reconstructed "
        "from vehicle positions, not official times."
    )


def headway_summary(hw: pd.DataFrame, line: str, direction: int | None) -> dict[str, float | None]:
    """Shares over valid headways of the selection (n-weighted), dropped counts and coverage."""
    sel = _select(hw, line, direction)
    usable = sel[~sel["data_outage"]]
    w = usable["n_headways"].astype(float)
    dropped = int(
        usable[
            [
                "n_dropped_train_between",
                "n_dropped_unproven_order",
                "n_dropped_no_scheduled_headway",
                "n_dropped_implausible",
            ]
        ]
        .sum()
        .sum()  # fmt: skip
    )
    return {
        "n": float(w.sum()),
        "pct_within_band": _wmean(usable["pct_within_band"], w),
        "pct_bunched": _wmean(usable["pct_bunched"], w),
        "pct_gap": _wmean(usable["pct_gap"], w),
        "median_ratio": _wmean(usable["median_ratio"], w),
        "n_dropped": float(dropped),
        "n_excluded_outage": float(sel["n_excluded_outage"].sum()),
        "n_disruption_hours": float(usable["service_disruption"].sum()),
        "coverage_pct": _cov(usable["coverage_numerator"], usable["coverage_denominator"]),
    }


def headway_by_hour(hw: pd.DataFrame, line: str, direction: int | None) -> pd.DataFrame:
    """Share within band per local hour of day (all days), outage hours left out."""
    sel = _select(hw, line, direction)
    sel = sel[~sel["data_outage"]]
    rows = []
    for hour, g in sel.groupby("hour_local"):
        w = g["n_headways"].astype(float)
        rows.append(
            {
                "hour_local": int(hour),
                "n": int(w.sum()),
                "pct_within_band": _wmean(g["pct_within_band"], w),
                "coverage_pct": _cov(g["coverage_numerator"], g["coverage_denominator"]),
            }
        )
    return pd.DataFrame(rows, columns=["hour_local", "n", "pct_within_band", "coverage_pct"])


def ett_by_band(ett: pd.DataFrame, line: str, direction: int | None) -> pd.DataFrame:
    """ETT per time band on weekdays: n-weighted median and p90, with coverage."""
    sel = _select(ett, line, direction)
    sel = sel[(sel["day_type"] == "weekday") & sel["time_band"].isin(TIME_BANDS)]
    rows = []
    for band in TIME_BANDS:
        g = sel[sel["time_band"] == band]
        w = g["n_trips"].astype(float)
        rows.append(
            {
                "time_band": BAND_LABELS[band],
                "n": int(w.sum()),
                "median_ett_s": _wmean(g["median_ett_s"], w),
                "p90_ett_s": _wmean(g["p90_ett_s"], w),
                "coverage_pct": _cov(g["coverage_numerator"], g["coverage_denominator"]),
            }
        )
    return pd.DataFrame(rows)


def shown(value: float | None, coverage_pct: float | None) -> float | None:
    """Withhold a value below the lowest coverage bar (same rule as the metric cards)."""
    if coverage_pct is None or pd.isna(coverage_pct) or coverage_pct < LOW_MIN:
        return None
    return value
