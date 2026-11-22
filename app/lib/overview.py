"""Per-line summary for the Overview page.

The gold hourly table holds medians per hour, which cannot be merged exactly. Summaries
here are the average of the hourly values weighted by the number of eligible predictions
(an approximation of the median over the window). Coverage and skipped share are exact
ratios of sums.
"""

import pandas as pd

from lib.timefmt import hour_start_utc

SUMMARY_COLUMNS = [
    "line",
    "median_deviation_s",
    "p90_deviation_s",
    "median_revision_s",
    "coverage_pct",
    "skipped_share",
    "n_eligible",
    "hours",
]


def _weighted(values: pd.Series, weights: pd.Series) -> float | None:
    mask = values.notna() & (weights > 0)
    if not mask.any():
        return None
    return float((values[mask] * weights[mask]).sum() / weights[mask].sum())


def recent_hours(hourly: pd.DataFrame, window_hours: int = 24) -> pd.DataFrame:
    """Rows of the hourly table whose hour starts within `window_hours` of the latest hour."""
    if hourly.empty:
        return hourly
    starts = pd.Series(
        [
            hour_start_utc(d, int(h))
            for d, h in zip(hourly["service_date"], hourly["hour_local"], strict=True)
        ],
        index=hourly.index,
    )
    return hourly[starts > starts.max() - pd.Timedelta(hours=window_hours)]


def line_summary(hourly: pd.DataFrame, window_hours: int = 24) -> pd.DataFrame:
    """One row per line over the last `window_hours`, all directions combined."""
    rows: list[dict[str, object]] = []
    for line, g in recent_hours(hourly, window_hours).groupby("line"):
        w = g["n_eligible"].astype(float)
        denom = float(g["observable_trip_stops"].sum())
        rows.append(
            {
                "line": str(line),
                "median_deviation_s": _weighted(g["median_predicted_deviation_s"], w),
                "p90_deviation_s": _weighted(g["p90_predicted_deviation_s"], w),
                "median_revision_s": _weighted(g["median_abs_revision_s"], w),
                "coverage_pct": 100.0 * float(g["coverage_numerator"].sum()) / denom
                if denom
                else None,
                "skipped_share": float(g["skipped_stop_count"].sum()) / denom if denom else None,
                "n_eligible": int(g["n_eligible"].sum()),
                "hours": int(g[["service_date", "hour_local"]].drop_duplicates().shape[0]),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)
