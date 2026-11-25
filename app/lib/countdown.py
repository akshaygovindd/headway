"""Frames for the countdown accuracy section of the Accuracy page (metric 10).

The marts hold counts, so rows combine exactly: a line is the sum of its stations and "all
exported days" is the sum of the daily rows. Percentages and coverage are always recomputed from
the summed counts, never averaged.
"""

import pandas as pd

from lib.accuracy import ALL_DAYS

COUNTDOWNS = [1, 3, 5, 10]
LABEL = "MBTA's published predictions (the data behind apps and signs)"
COUNT_COLUMNS = ["n", "n_arrived", "n_non_arrival", "n_unknown", "n_excluded_outage",
                 "n_disruption", "n_within_60s", "n_within_120s", "coverage_numerator",
                 "coverage_denominator"]  # fmt: skip
OUT_COLUMNS = ["countdown_min", "n", "n_non_arrival", "n_unknown", "n_excluded_outage",
               "n_disruption", "pct_within_60s", "pct_within_120s", "coverage_pct"]  # fmt: skip


def source(win: pd.DataFrame | None, daily: pd.DataFrame | None, window: str) -> pd.DataFrame:
    """The rows behind a window choice: the exact window table, or every exported day."""
    if window == ALL_DAYS:
        return daily if daily is not None else pd.DataFrame()
    if win is None or win.empty:
        return pd.DataFrame()
    return win[win["window_label"] == window]


def _combine(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=[*keys, *OUT_COLUMNS[1:]])
    g = frame.groupby(keys, as_index=False)[COUNT_COLUMNS].sum()
    n = g["n"].where(g["n"] > 0)
    den = g["coverage_denominator"].where(g["coverage_denominator"] > 0)
    g["pct_within_60s"] = 100.0 * g["n_within_60s"] / n
    g["pct_within_120s"] = 100.0 * g["n_within_120s"] / n
    g["coverage_pct"] = 100.0 * g["coverage_numerator"] / den
    g["countdown_min"] = pd.Categorical(g["countdown_min"], COUNTDOWNS, ordered=True)
    return g.sort_values([*keys]).reset_index(drop=True)


def by_line(frame: pd.DataFrame, line: str) -> pd.DataFrame:
    """One row per countdown bucket for the line, all stations combined."""
    return _combine(frame[frame["line"] == line], ["countdown_min"])


def by_station(frame: pd.DataFrame, line: str) -> pd.DataFrame:
    """One row per station and countdown bucket for the line."""
    out = _combine(frame[frame["line"] == line], ["station_name", "countdown_min"])
    return out.sort_values(["station_name", "countdown_min"]).reset_index(drop=True)


def unchecked_note(rows: pd.DataFrame) -> str:
    """Counts beside the numbers: countdowns left out because they could not be checked."""
    unknown = int(rows["n_unknown"].sum()) if not rows.empty else 0
    misses = int(rows["n_non_arrival"].sum()) if not rows.empty else 0
    return (
        f"{unknown:,} countdowns could not be checked (no reconstructed arrival and no confirmed "
        f"cancellation) and are left out of the percentages; {misses:,} counted countdowns were "
        "confirmed non-arrivals and count as misses."
    )
