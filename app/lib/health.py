"""Frames for the Pipeline health page."""

from datetime import UTC, datetime

import pandas as pd

from lib.timefmt import date_label, hour_label, local_date, to_local

STATUS_ORDER = ("fail", "warn", "no_baseline", "no_data", "ok")
FEEDS = ("trip_updates", "vehicle_positions", "alerts")
QUARANTINE_REASONS = ("missing_trip_id", "invalid_coordinates", "protobuf_parse_error", "other")


def check_status_grid(checks: pd.DataFrame, hours: int = 12) -> pd.DataFrame:
    """Status (ok/warn/fail/no_baseline/no_data) per check for the latest `hours` hours."""
    if checks.empty:
        return pd.DataFrame()
    keyed = checks.assign(
        label=[
            f"{date_label(local_date(d, int(h)))} {hour_label(int(h))}"
            for d, h in zip(checks["service_date"], checks["hour_local"], strict=True)
        ],
        order=[
            (local_date(d, int(h)).toordinal(), int(h))
            for d, h in zip(checks["service_date"], checks["hour_local"], strict=True)
        ],
    )
    latest = sorted(keyed["order"].unique())[-hours:]
    keyed = keyed[keyed["order"].isin(latest)]
    labels = keyed.drop_duplicates("order").sort_values("order")["label"].tolist()
    grid = keyed.pivot(index="check_name", columns="label", values="status")
    return grid.reindex(columns=labels)


def check_summary(checks: pd.DataFrame, hours: int = 12) -> dict[str, int]:
    """Number of check results per status over the latest `hours` hours."""
    grid = check_status_grid(checks, hours)
    counts = dict.fromkeys(STATUS_ORDER, 0)
    for value in grid.to_numpy().ravel():
        if isinstance(value, str):
            counts[value] = counts.get(value, 0) + 1
    return counts


def captures_per_hour(pipeline: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Ingest runs per UTC hour against the runs expected between the first and last run.

    Columns: hour_start_utc, label (local), runs, expected, missed, partial. The first and last
    hours are clipped to the observed range so edges are not counted as gaps; `partial` marks
    the hour that is still in progress at `now`.
    """
    cols = ["hour_start_utc", "label", "runs", "expected", "missed", "partial"]
    if pipeline.empty:
        return pd.DataFrame(columns=cols)
    ts = pd.to_datetime(pipeline["scheduled_at"], utc=True).dt.floor("min")
    first, last = ts.min(), ts.max()
    minutes = pd.date_range(first, last, freq="min")
    expected = pd.Series(1, index=minutes).groupby(minutes.floor("h")).sum()
    runs = pipeline.assign(h=ts.dt.floor("h")).groupby("h")["run_id"].nunique()
    frame = pd.DataFrame({"expected": expected}).join(runs.rename("runs")).fillna({"runs": 0})
    frame["runs"] = frame["runs"].astype(int)
    frame["missed"] = (frame["expected"] - frame["runs"]).clip(lower=0)
    frame = frame.reset_index(names="hour_start_utc")
    frame["label"] = [
        to_local(t.to_pydatetime()).strftime("%Y-%m-%d %H:00") for t in frame["hour_start_utc"]
    ]
    current = pd.Timestamp(now or datetime.now(UTC)).floor("h")
    frame["partial"] = frame["hour_start_utc"] == current
    return frame[cols]


def quarantine_by_reason(pipeline: pd.DataFrame) -> pd.DataFrame:
    """Quarantined record counts by reason over all runs.

    Uses the per-reason columns the gold has. When the gold only carries a total (or fewer
    reasons than expected), the remainder is shown as "not itemised in this export".
    """
    present = [r for r in QUARANTINE_REASONS if f"quarantine_{r}" in pipeline.columns]
    rows = [{"reason": r, "records": int(pipeline[f"quarantine_{r}"].sum())} for r in present]
    if "quarantine_total" in pipeline.columns:
        rest = int(pipeline["quarantine_total"].sum()) - sum(int(x["records"]) for x in rows)  # type: ignore[call-overload]
        if rest > 0 or not rows:
            rows.append({"reason": "not itemised in this export", "records": max(rest, 0)})
    return pd.DataFrame(rows, columns=["reason", "records"])


def duplicates_by_feed(pipeline: pd.DataFrame) -> pd.DataFrame:
    """Duplicate captures per feed (payload identical to the previous capture)."""
    rows = [
        {
            "feed": feed,
            "captures": int(pipeline[f"{feed}_status"].notna().sum()),
            "duplicates": int(pipeline[f"{feed}_is_duplicate"].sum()),
        }
        for feed in FEEDS
    ]
    return pd.DataFrame(rows)


def check_series(checks: pd.DataFrame, check_name: str) -> pd.DataFrame:
    """Hourly values of one check with plain-string labels, oldest first."""
    sel = checks[checks["check_name"] == check_name]
    frame = pd.DataFrame(
        {
            "order": [
                (local_date(d, int(h)).toordinal(), int(h))
                for d, h in zip(sel["service_date"], sel["hour_local"], strict=True)
            ],
            "label": [
                f"{date_label(local_date(d, int(h)))} {hour_label(int(h))}"
                for d, h in zip(sel["service_date"], sel["hour_local"], strict=True)
            ],
            "value": sel["value"].to_numpy(),
            "status": sel["status"].to_numpy(),
        }
    )
    return frame.sort_values("order").drop(columns="order").reset_index(drop=True)


def cost_summary(cost: pd.DataFrame) -> dict[str, object]:
    """Latest-day and 7-day totals, cost per 1,000 captures, and the latest day by service."""
    day_totals = cost.drop_duplicates("date").sort_values("date")
    latest = day_totals.iloc[-1]
    latest_services = cost[cost["date"] == latest["date"]][["service", "cost_usd"]]
    per_1000 = latest["cost_per_1000_captures"]
    return {
        "latest_date": latest["date"],
        "latest_total_usd": float(latest["day_total_usd"]),
        "last_7_days_usd": float(day_totals.tail(7)["day_total_usd"].sum()),
        "cost_per_1000_captures": None if pd.isna(per_1000) else float(per_1000),
        "by_service": latest_services.sort_values("cost_usd", ascending=False).reset_index(
            drop=True
        ),
    }


SLO_LABELS = {
    "capture_completeness": "Capture completeness (minutes)",
    "gold_freshness": "Gold freshness ≤ 90 min (hours)",
    "transform_success": "Transform success (scheduled runs)",
    "arrival_coverage": "Arrival coverage (trip stops)",
    "arrival_coverage_disrupted": "Arrival coverage, lines under a service disruption",
}


def _slo_cell(pct: float | None, met: bool | None, budget: float | None = None) -> str:
    if pct is None or pd.isna(pct):
        return "no data yet (no runs recorded)"
    mark = "✅" if met else "❌"
    text = f"{mark} {pct:.2f}%"
    if budget is not None and not pd.isna(budget):
        text += f" · budget {100 * budget:.0f}%"
    return text


def slo_hours(rollup: pd.DataFrame, window: str) -> float | None:
    """Hours of capture the window rests on: the minutes of the capture-completeness objective."""
    sel = rollup[
        (rollup["slo_name"] == "capture_completeness") & (rollup["window_label"] == window)
    ]
    if sel.empty or pd.isna(sel.iloc[0]["total_events"]):
        return None
    return float(sel.iloc[0]["total_events"]) / 60.0


def _hours_label(name: str, hours: float | None) -> str:
    return name if hours is None else f"{name} (based on {hours:.0f} h)"


def slo_table(daily: pd.DataFrame, rollup: pd.DataFrame) -> pd.DataFrame:
    """One row per objective, always all of them: the latest service date, the last 7 and the
    last 30 dates with the error budget used over the window, the columns labelled with the hours
    the windows cover. Objectives that do not count toward the SLO (the disrupted-lines arrival
    coverage) come last. Objectives without events say "no data yet (no runs recorded)"."""
    cols = ["Objective", "Target", "Latest date", "Last 7 days", "Last 30 days", "Counts"]
    if "slo_name" not in rollup:
        rollup = pd.DataFrame(columns=["slo_name", "window_label", "target_pct", "total_events"])
    if "slo_name" not in daily:
        daily = pd.DataFrame(columns=["slo_name", "service_date", "target_pct"])
    if "total_events" in rollup:
        h7, h30 = slo_hours(rollup, "last_7d"), slo_hours(rollup, "last_30d")
    else:
        h7 = h30 = None
    seven, thirty = _hours_label("Last 7 days", h7), _hours_label("Last 30 days", h30)
    targets = {}
    if not daily.empty:
        targets = dict(zip(daily["slo_name"], daily["target_pct"], strict=False))
    last = daily["service_date"].max() if not daily.empty else None
    rows = []
    for name, label in SLO_LABELS.items():
        d = daily[(daily["slo_name"] == name) & (daily["service_date"] == last)]
        r = rollup[rollup["slo_name"] == name].set_index("window_label")

        def cell(window: str, r: pd.DataFrame = r) -> str:
            if window not in r.index:
                return _slo_cell(None, None)
            w = r.loc[window]
            return _slo_cell(w["actual_pct"], w["met"], w["error_budget_used"])

        latest = (
            _slo_cell(None, None)
            if d.empty
            else _slo_cell(d.iloc[0]["actual_pct"], d.iloc[0]["met"])
        )
        target = targets.get(name, r["target_pct"].iloc[0] if not r.empty else None)
        counts = name != "arrival_coverage_disrupted"
        rows.append(
            {"Objective": label, "Target": "n/a" if target is None else f"{target:.0f}%",
             "Latest date": latest, "Last 7 days": cell("last_7d"),
             "Last 30 days": cell("last_30d"),
             "Counts": "yes" if counts else "reported separately"}
        )  # fmt: skip
    out = pd.DataFrame(rows, columns=cols).rename(
        columns={"Last 7 days": seven, "Last 30 days": thirty}
    )
    return out.sort_values("Counts", key=lambda s: s != "yes", kind="stable").reset_index(drop=True)
