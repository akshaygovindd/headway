"""Frames and wording of the Incidents page (Release 3).

An incident is a rule result (docs/METRICS.md "Incidents"), its summary is generated from the
evidence and checked against it, and its match status says whether a line-scope service alert
overlapped it. Nothing here says an incident or an alert was "missed" or "phantom". The withholding
rule of every page applies: no value from an hour under 50% coverage.
"""

import json
from typing import Any

import pandas as pd

from lib.coverage import is_withheld

RULES = {
    "deviation": "deviation above the usual",
    "gap": "headway gaps",
    "skipped": "skipped stops",
    "alert": "service alert",
}
MATCH_LABELS = {
    "matched": "matched: a line-scope service alert overlapped this incident",
    "unmatched_incident": "unmatched incident: no line-scope service alert overlapped it",
}
HANDCHECK_NOTE = (
    "Match status is the result of a rule (same line, time overlap within 30 minutes), not a "
    "verdict. A manual review of a sample of matches and non-matches is pending; its result goes "
    "to docs/MEASUREMENTS.md (Alert vs reality: hand check)."
)
SUMMARY_WITHHELD = "Summary withheld: arrival coverage was under 50% in at least one hour."


def rules_text(crossed: str) -> str:
    """The thresholds crossed, in words."""
    parts = [RULES.get(r, r) for r in str(crossed).split(",") if r]
    return ", ".join(parts) if parts else "none"


def incident_list(frame: pd.DataFrame) -> pd.DataFrame:
    """Incidents, open first and then the most recent first."""
    return frame.sort_values(
        ["is_open", "incident_start_ts"], ascending=[False, False]
    ).reset_index(drop=True)


def evidence(evidence_json: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(evidence_json)
    return doc


def evidence_hours(doc: dict[str, Any]) -> pd.DataFrame:
    """One row per breach hour of the evidence, with what the page shows."""
    cols = [
        "hour", "median_deviation_s", "baseline_median_s", "deviation_over_baseline_s",
        "gap_share_pct", "skipped_share_pct", "coverage_pct", "crossed",
    ]  # fmt: skip
    rows = doc.get("hours", [])
    out = pd.DataFrame(rows, columns=cols)
    out["crossed"] = out["crossed"].map(lambda c: ", ".join(RULES.get(r, r) for r in (c or [])))
    return out


def summary_shown(row: pd.Series) -> bool:
    """False when the summary would carry values from an hour under 50% coverage."""
    return not is_withheld(row["min_coverage_pct"])


def lag_text(seconds: float | None) -> str:
    """Alert lag in words: positive when the first matching alert came after the incident began."""
    if seconds is None or pd.isna(seconds):
        return "no alert lag"
    minutes = abs(seconds) / 60
    if round(minutes) == 0:
        return "the first matching alert was captured when the incident began"
    when = "after" if seconds > 0 else "before"
    return f"the first matching alert was captured {minutes:.0f} min {when} the incident began"


def day_summary(daily: pd.DataFrame | None) -> dict[str, float | None]:
    """Totals over the exported days for the matching counts and the median lag."""
    if daily is None or daily.empty:
        return {}
    matched = daily["n_incidents_matched"].sum()
    lagged = daily["n_with_lag"].sum()
    weighted = (daily["median_alert_lag_s"].fillna(0) * daily["n_with_lag"]).sum()
    return {
        "incidents": float(daily["n_incidents"].sum()),
        "incidents_matched": float(matched),
        "incidents_unmatched": float(daily["n_incidents_unmatched"].sum()),
        "alerts": float(daily["n_alerts"].sum()),
        "alerts_unmatched": float(daily["n_alerts_unmatched"].sum()),
        "mean_of_daily_median_lag_s": float(weighted / lagged) if lagged else None,
    }
