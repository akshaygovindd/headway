"""The local agg_forecast_backtest table (not exported, not a dbt model)."""

from pathlib import Path

import duckdb
import pandas as pd

TABLE = "agg_forecast_backtest"


def write_results(path: Path, run_id: str, results: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = results.assign(run_id=run_id)
    con = duckdb.connect(str(path))
    con.register("new_rows", out)
    con.execute(f"create table if not exists {TABLE} as select * from new_rows where false")  # noqa: S608
    con.execute(f"delete from {TABLE} where run_id = ?", [run_id])  # noqa: S608
    con.execute(f"insert into {TABLE} select * from new_rows")  # noqa: S608
    con.close()
