"""Publish exported gold tables to MotherDuck."""

import os
from collections.abc import Sequence
from typing import Any

from headway_transform import duckdb_limits

GOLD_TABLES = (
    "agg_line_reliability_hourly",
    "agg_line_reliability_window",
    "agg_stop_deviation_daily",
    "agg_pipeline_health",
    "agg_quality_checks_hourly",
    "agg_quarantine_reasons",
    "dim_route",
    "dim_stop",
    "route_shapes",
)
# Release 2 aggregates: exported and published only when HEADWAY_ENABLE_R2=true, like the models.
R2_GOLD_TABLES = (
    "agg_arrival_reconstruction_daily",
    "agg_prediction_accuracy_daily",
    "agg_actual_deviation_hourly",
    "agg_headway_adherence_hourly",
    "agg_ett_daily",
    "agg_active_alerts",
    "agg_accuracy_window",
    "agg_actual_deviation_window",
    "agg_headway_window",
    "agg_ett_window",
    "agg_actual_deviation_daily",
    "agg_headway_adherence_daily",
    "agg_slo_daily",
    "agg_slo_rollup",
    "agg_countdown_accuracy_window",
    "agg_countdown_accuracy_daily",
)
# Release 3 (HEADWAY_ENABLE_R3=true, with R2): the next-hour forecasts, the gate status, incidents,
# alert matching and the weather comparison. The backtest summary joins them only once a line has passed the training
# gate (R3_OPTIONAL_TABLES).
R3_GOLD_TABLES = (
    "agg_forecast_next_hour",
    "agg_forecast_gate_status",
    "agg_incidents",
    "agg_alert_matching_daily",
    "agg_weather_comparison",
)
R3_OPTIONAL_TABLES = ("agg_forecast_backtest",)
# Release 4 (HEADWAY_ENABLE_R4=true, with R2; independent of R3): the public site's segment health,
# last-hour Marey arrivals, hourly capture heartbeat, ETT per direction and time band and the
# line-scope service disruptions (public MBTA header text), the trip-time budget per station pair.
R4_GOLD_TABLES = (
    "agg_headway_segment_window",
    "agg_marey_last_hour",
    "agg_capture_heartbeat_hourly",
    "agg_ett_band_window",
    "agg_service_disruptions",
    "agg_trip_time_budget",
)
# cost_daily is written by cost.py, not dbt, so it is published only when it exists. The R3
# backtest is exported by dbt only when it has rows.
OPTIONAL_TABLES = ("cost_daily", *R3_OPTIONAL_TABLES)
DATABASE = "headway"


def r2_enabled() -> bool:
    return os.environ.get("HEADWAY_ENABLE_R2", "").lower() == "true"


def r3_enabled() -> bool:
    """Release 3 models (tag r3) build only when set (with R2); the forecast tables are exported."""
    return os.environ.get("HEADWAY_ENABLE_R3", "").lower() == "true"


def r4_enabled() -> bool:
    """Release 4 models (tag r4) build only when set (with R2); the site tables are exported."""
    return os.environ.get("HEADWAY_ENABLE_R4", "").lower() == "true"


def gold_tables() -> tuple[str, ...]:
    """The gold tables this deployment always exports: the base set, plus Release 2 when enabled,
    plus the Release 3 forecast tables and the Release 4 site tables when enabled with it."""
    tables: tuple[str, ...] = GOLD_TABLES + R2_GOLD_TABLES if r2_enabled() else GOLD_TABLES
    if r2_enabled() and r3_enabled():
        tables += R3_GOLD_TABLES
    if r2_enabled() and r4_enabled():
        tables += R4_GOLD_TABLES
    return tables


def _connect(token: str) -> Any:
    return duckdb_limits.connect(
        "md:",
        config={
            "motherduck_token": token,
            "home_directory": "/tmp",
            "extension_directory": os.environ.get(
                "HEADWAY_DUCKDB_EXTENSIONS", "/tmp/duckdb_extensions"
            ),
        },
    )


def history_glob(history_url: str, table: str) -> str:
    return f"{history_url}/table={table}/metric_version=*/service_date=*/part.parquet"


def publish_motherduck(
    token: str,
    gold_url: str,
    tables: Sequence[str],
    connect: Any = None,
    history_url: str | None = None,
    history_tables: Sequence[str] = (),
) -> list[str]:
    """CREATE OR REPLACE one MotherDuck table per exported parquet file.

    With `history_url`, every history table that has objects also becomes `history_<table>`: all
    metric versions (the version is a column, read from the hive path), all closed days.
    Returns the names published.
    """
    con = (connect or _connect)(token)
    published = list(tables)
    try:
        con.execute("create secret (type s3, provider credential_chain)")
        con.execute(f"create database if not exists {DATABASE}")
        con.execute(f"use {DATABASE}")
        for table in tables:
            con.execute(
                f"create or replace table main.{table} as "
                f"select * from read_parquet('{gold_url}/{table}.parquet')"
            )
        for table in history_tables if history_url else ():
            pattern = history_glob(str(history_url), table)
            found = con.execute(f"select count(*) from glob('{pattern}')").fetchone()
            if not found or not found[0]:
                continue
            con.execute(
                f"create or replace table main.history_{table} as "
                f"select * from read_parquet('{pattern}', hive_partitioning = true, "
                "union_by_name = true)"
            )
            published.append(f"history_{table}")
    finally:
        con.close()
    return published
