"""Publish exported gold tables to MotherDuck."""

import os
from collections.abc import Sequence
from typing import Any

import duckdb

GOLD_TABLES = (
    "agg_line_reliability_hourly",
    "agg_line_reliability_window",
    "agg_stop_deviation_daily",
    "agg_pipeline_health",
    "agg_quality_checks_hourly",
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
)
# cost_daily is written by cost.py, not dbt, so it is published only when it exists.
OPTIONAL_TABLES = ("cost_daily",)
DATABASE = "headway"


def r2_enabled() -> bool:
    return os.environ.get("HEADWAY_ENABLE_R2", "").lower() == "true"


def gold_tables() -> tuple[str, ...]:
    """The gold tables this deployment exports: the base set, plus Release 2 when enabled."""
    return GOLD_TABLES + R2_GOLD_TABLES if r2_enabled() else GOLD_TABLES


def _connect(token: str) -> Any:
    return duckdb.connect(
        "md:",
        config={
            "motherduck_token": token,
            "home_directory": "/tmp",
            "extension_directory": os.environ.get(
                "HEADWAY_DUCKDB_EXTENSIONS", "/tmp/duckdb_extensions"
            ),
        },
    )


def publish_motherduck(
    token: str, gold_url: str, tables: Sequence[str], connect: Any = None
) -> list[str]:
    """CREATE OR REPLACE one MotherDuck table per exported parquet file."""
    con = (connect or _connect)(token)
    try:
        con.execute("create secret (type s3, provider credential_chain)")
        con.execute(f"create database if not exists {DATABASE}")
        con.execute(f"use {DATABASE}")
        for table in tables:
            con.execute(
                f"create or replace table main.{table} as "
                f"select * from read_parquet('{gold_url}/{table}.parquet')"
            )
    finally:
        con.close()
    return list(tables)
