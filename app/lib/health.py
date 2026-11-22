"""Frames for the Pipeline health page."""

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


def captures_per_hour(pipeline: pd.DataFrame) -> pd.DataFrame:
    """Ingest runs per UTC hour against the runs expected between the first and last run.

    Columns: hour_start_utc, label (local), runs, expected, missed. The first and last
    hours are clipped to the observed range so edges are not counted as gaps.
    """
    cols = ["hour_start_utc", "label", "runs", "expected", "missed"]
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
    return frame[cols]


def quarantine_by_reason(pipeline: pd.DataFrame) -> pd.DataFrame:
    """Quarantined record counts by reason over all runs."""
    return pd.DataFrame(
        {
            "reason": list(QUARANTINE_REASONS),
            "records": [int(pipeline[f"quarantine_{r}"].sum()) for r in QUARANTINE_REASONS],
        }
    )


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
