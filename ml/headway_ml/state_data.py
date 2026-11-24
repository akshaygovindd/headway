"""Training data from the closed-day partitions the transform keeps in the raw bucket.

    state/forecast_examples/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet
    state/forecast_gate_days/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet

Partitions are downloaded to a work directory and read with DuckDB; nothing is built here. The
versions are the transform's (headway_transform/forecast_state.py); a test pins them together.
"""

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from headway_ml import data

EXAMPLES = ("forecast_examples", 1)
GATE_DAYS = ("forecast_gate_days", 1)


def _partitions(s3: Any, bucket: str, name: str, version: int) -> dict[date, str]:
    prefix = f"state/{name}/metric_version={version}/"
    found: dict[date, str] = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            m = re.fullmatch(
                rf"{re.escape(prefix)}service_date=(\d{{4}}-\d{{2}}-\d{{2}})/part\.parquet",
                obj["Key"],
            )
            if m:
                found[date.fromisoformat(m.group(1))] = obj["Key"]
    return found


SAMPLE_SEED = 7


def _read_partitions(
    s3: Any, bucket: str, keys: list[str], workdir: Path, max_rows: int | None
) -> pd.DataFrame:
    """Read the partitions one at a time (each file is removed after it is read). `max_rows` keeps
    at most that many rows per partition, a reproducible reservoir sample: the examples of one day
    are strongly correlated (every trip stop of every slot), and the job's memory and time grow
    with the rows."""
    con = duckdb.connect()
    con.execute("set TimeZone = 'UTC'")
    frames = []
    try:
        for i, key in enumerate(keys):
            path = workdir / f"{i:05d}.parquet"
            s3.download_file(bucket, key, str(path))
            query = "select * from read_parquet(?)"
            if max_rows is not None:
                query += f" using sample reservoir({int(max_rows)} rows) repeatable ({SAMPLE_SEED})"
            frames.append(con.execute(query, [str(path)]).df())
            path.unlink()
    finally:
        con.close()
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load(
    s3: Any, bucket: str, workdir: Path, window_days: int, max_rows_per_day: int | None = None
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(examples of the last `window_days` days before the newest partition, gate days of all
    partitions). Either may be empty."""
    workdir.mkdir(parents=True, exist_ok=True)
    ex = _partitions(s3, bucket, *EXAMPLES)
    gd = _partitions(s3, bucket, *GATE_DAYS)
    if ex:
        newest = max(ex)
        keep = [ex[d] for d in sorted(ex) if d > newest - timedelta(days=window_days)]
        examples = _read_partitions(s3, bucket, keep, workdir, max_rows_per_day)
        examples = examples[examples["label_delay_s"].notna()].reset_index(drop=True)
        examples["service_date"] = pd.to_datetime(examples["service_date"]).dt.date
        examples["active_disruption_alert"] = (
            examples["active_disruption_alert"].fillna(False).astype(bool)
        )
        examples = data.add_period(examples)
    else:
        examples = pd.DataFrame()
    gate_days = _read_partitions(s3, bucket, [gd[d] for d in sorted(gd)], workdir, None)
    return examples, gate_days
