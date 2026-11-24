"""Read labelled examples and the training gate from a DuckDB built with HEADWAY_ENABLE_R3."""

from pathlib import Path

import duckdb
import pandas as pd

FEATURES = [
    "line", "direction_id", "horizon_min", "stop_position", "hour_local", "day_type",
    "scheduled_headway_s", "last_upstream_delay_s", "rolling_median_delay_30m",
    "rolling_median_delay_60m", "headway_within_share", "active_disruption_alert",
    "skipped_share_1h", "mbta_predicted_deviation_s",
]  # fmt: skip
KEY = ["line", "horizon_min", "prediction_ts"]


def load_examples(db: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    con.execute("set TimeZone = 'UTC'")
    df = con.execute(
        "select prediction_ts, service_date, direction_id, "  # noqa: S608
        + ", ".join(c for c in FEATURES if c != "direction_id")
        + ", label_delay_s from fct_forecast_examples where label_delay_s is not null"
    ).df()
    con.close()
    df["service_date"] = pd.to_datetime(df["service_date"]).dt.date
    df["active_disruption_alert"] = df["active_disruption_alert"].fillna(False).astype(bool)
    return add_period(df)


def add_period(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["period_type"] = df["active_disruption_alert"].map({True: "alert_active", False: "normal"})
    return df


def load_gate(db: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    gate = con.execute("select * from agg_forecast_gate").df()
    con.close()
    return gate
