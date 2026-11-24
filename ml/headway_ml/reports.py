"""The training job's published summaries, read by the transform and exported to gold.

    models/forecast/reports/backtest.parquet     pooled backtest (agg_forecast_backtest source)
    models/forecast/reports/gate_status.parquet  gate per line (agg_forecast_gate_status source)

Written by every run that has data, also one that only produced baselines. The transform keeps
only lines past the gate and exports the backtest only when some line has passed it
(dbt/headway/models/marts/agg_forecast_backtest.sql, macros/export_gold.sql).
"""

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from headway_ml import backtest, registry

PREFIX = f"{registry.PREFIX}/reports"
BACKTEST_KEY = f"{PREFIX}/backtest.parquet"
GATE_KEY = f"{PREFIX}/gate_status.parquet"


def backtest_frame(results: pd.DataFrame, run_id: str, trained_at: str) -> pd.DataFrame:
    """Pooled rows (all folds) of every period, both views."""
    pooled = results[results["fold"] == "all"]
    return pooled[backtest.COLUMNS].assign(run_id=run_id, trained_at=trained_at)


def gate_frame(gate: pd.DataFrame, run_id: str, trained_at: str) -> pd.DataFrame:
    cols = ["line", "n_days", "n_weekend_days", "min_days", "min_weekend_days", "gate_passed"]
    return gate[cols].assign(run_id=run_id, trained_at=trained_at)


def _put(s3: Any, bucket: str, key: str, df: pd.DataFrame) -> None:
    con = duckdb.connect()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "part.parquet"
            con.register("frame", df)
            con.execute(f"copy frame to '{path}' (format parquet)")
            s3.upload_file(str(path), bucket, key)
    finally:
        con.close()


def write(
    s3: Any, bucket: str, run_id: str, results: pd.DataFrame, gate: pd.DataFrame
) -> list[str]:
    trained_at = datetime.now(UTC).isoformat()
    _put(s3, bucket, BACKTEST_KEY, backtest_frame(results, run_id, trained_at))
    _put(s3, bucket, GATE_KEY, gate_frame(gate, run_id, trained_at))
    return [BACKTEST_KEY, GATE_KEY]
