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
