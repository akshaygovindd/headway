"""Per-line summary for the Overview page, read from agg_line_reliability_window.

The mart computes exact medians and percentiles over all eligible predictions in the
window, so nothing is averaged or recomputed here.
"""

import pandas as pd

WINDOW_LABEL = "last_24h"

SUMMARY_COLUMNS = [
    "line",
    "median_deviation_s",
    "p90_deviation_s",
    "median_revision_s",
    "coverage_pct",
    "skipped_share",
    "n_eligible",
]

_RENAME = {
    "median_predicted_deviation_s": "median_deviation_s",
    "p90_predicted_deviation_s": "p90_deviation_s",
    "median_abs_revision_s": "median_revision_s",
}


def line_summary(window: pd.DataFrame, window_label: str = WINDOW_LABEL) -> pd.DataFrame:
    """One row per line for the given window label."""
    rows = window[window["window_label"] == window_label].rename(columns=_RENAME)
    return rows[SUMMARY_COLUMNS].reset_index(drop=True)
