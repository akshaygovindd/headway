"""Weather comparison wording (Release 3, ADR-008, ADR-022).

Descriptive only: two groups of days are compared, nothing says why they differ. Every number in a
sentence is a value of the row it describes; a withheld row says so instead of showing values.
"""

from dataclasses import dataclass

import pandas as pd

CONDITION_LABELS = {
    "snow": "snow days",
    "heavy_rain": "heavy-rain days",
    "extreme_heat": "extreme-heat days",
}
METRIC_PHRASES = {
    "on_time": "on-time share",
    "headway_within_band": "share of headways within the scheduled band",
}
ORDER = {"snow": 0, "heavy_rain": 1, "extreme_heat": 2}
NOTE = (
    "Days are grouped by weather at Boston Logan (NOAA daily summary): snow is 25.4 mm or more of "
    "snowfall, heavy rain 12.7 mm or more of precipitation on a day without snow, extreme heat a "
    "maximum of 32.2 C or more. Each figure pools the arrivals or headways of the days in its "
    "group and the interval is a 95% bootstrap over days. This compares groups of days and says "
    "nothing about why they differ. Weather data trails real time by about a week, and one "
    "station stands for the whole network."
)


@dataclass(frozen=True)
class Statement:
    condition: str
    metric: str
    text: str
    withheld: bool


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def statements(table: pd.DataFrame) -> list[Statement]:
    """One sentence per condition and metric, in a stable order; withheld rows carry no values."""
    out = []
    ordered = table.assign(_o=table["condition"].map(ORDER)).sort_values(["_o", "metric"])
    for _, r in ordered.iterrows():
        label = CONDITION_LABELS.get(str(r["condition"]), str(r["condition"]))
        phrase = METRIC_PHRASES.get(str(r["metric"]), str(r["metric"]))
        nc, no = int(r["n_days_condition"]), int(r["n_days_other"])
        if bool(r["withheld"]):
            reason = (
                r["withheld_reason"] if isinstance(r["withheld_reason"], str) else "too few days"
            )
            out.append(
                Statement(
                    r["condition"],
                    r["metric"],
                    f"On {label}, the {phrase} is not shown: {reason} "
                    f"({nc} {label} and {no} other days in the window).",
                    True,
                )
            )
            continue
        text = (
            f"On {label}, the {phrase} was {_pct(r['value_condition'])} vs "
            f"{_pct(r['value_other'])} on other days ({nc} vs {no} days; 95% intervals "
            f"{_pct(r['ci_lo_condition'])} to {_pct(r['ci_hi_condition'])} and "
            f"{_pct(r['ci_lo_other'])} to {_pct(r['ci_hi_other'])})."
        )
        out.append(Statement(r["condition"], r["metric"], text, False))
    return out


def has_no_days(table: pd.DataFrame) -> bool:
    """True when no day of either kind was compared (no weather data in the window yet)."""
    return bool((table["n_days_condition"] + table["n_days_other"]).sum() == 0)


def window_text(table: pd.DataFrame) -> str | None:
    """The service dates the published rows cover, or None when no row compared anything."""
    start = table["window_start"].dropna()
    end = table["window_end"].dropna()
    if start.empty or end.empty:
        return None
    return f"Window: {pd.Timestamp(start.min()):%Y-%m-%d} to {pd.Timestamp(end.max()):%Y-%m-%d}."
