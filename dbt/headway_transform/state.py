"""Run state that outlives the Lambda container: small tables kept as Parquet in the raw bucket.

A transform runs in a fresh /tmp, so anything that has to span runs (which hours succeeded, how
long they took) cannot live in the DuckDB file. A `StateTable` is stored under
``s3://<raw>/state/<folder>/dt=YYYY-MM-DD/rows.parquet``, one object per UTC day of the row's
timestamp. At startup the last `retention_days` of it are loaded into the local DuckDB file as
`main.<name>`, where dbt models read it; at the end of a run its rows are appended to the day's
object. Runs are serialised by the transform lock, so read-modify-write of a day is safe, and
nothing here uses MotherDuck. Any other table that must persist (the Elementary result tables in
2.5) declares a `StateTable` and uses the same three functions.
"""

import logging
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

logger = logging.getLogger()
PARQUET = "application/vnd.apache.parquet"


@dataclass(frozen=True)
class StateTable:
    name: str  # the DuckDB table in schema main
    folder: str  # the folder under state/
    columns: Mapping[str, str]  # column -> DuckDB type, in order
    timestamp: str  # column that dates a row (partition and retention)
    retention_days: int = 35

    @property
    def prefix(self) -> str:
        return f"state/{self.folder}"

    def key(self, day: date) -> str:
        return f"{self.prefix}/dt={day.isoformat()}/rows.parquet"

    @property
    def ddl(self) -> str:
        cols = ", ".join(f"{c} {t}" for c, t in self.columns.items())
        return f"create or replace table main.{self.name} ({cols})"


# One row per transform invocation. `slot` is the scheduled hour the run belongs to (the schedule
# fires at :20, so a run is assigned to the hour of `started_at` minus 20 minutes).
TRANSFORM_RUNS = StateTable(
    name="agg_transform_runs",
    folder="transform_runs",
    timestamp="started_at",
    columns={
        "run_id": "varchar",
        "task": "varchar",
        "slot": "timestamptz",
        "started_at": "timestamptz",
        "finished_at": "timestamptz",
        "status": "varchar",
        "tests_passed": "integer",
        "tests_failed": "integer",
        "rows_exported": "bigint",
        "gold_exported_at": "timestamptz",
        "gold_published": "boolean",
        "r2_enabled": "boolean",
        "duration_ms": "bigint",
        "step_ms": "varchar",
        "error": "varchar",
    },
)


def slot_of(started_at: datetime, minute: int = 20) -> datetime:
    """The scheduled hour (UTC) a run belongs to: the hour of `started_at` minus `minute`."""
    return (started_at.astimezone(UTC) - timedelta(minutes=minute)).replace(
        minute=0, second=0, microsecond=0
    )


def _get(s3: Any, bucket: str, key: str) -> bytes | None:
    try:
        return bytes(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None


def fetch_files(s3: Any, bucket: str, table: StateTable, now: datetime, into: Path) -> list[Path]:
    """Download the objects of the last `retention_days` days (UTC) into `into`, oldest first."""
    first = now.astimezone(UTC).date() - timedelta(days=table.retention_days)
    keys: list[str] = []
    pages = s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=table.prefix + "/")
    for page in pages:
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith("/rows.parquet")]
    files: list[Path] = []
    for i, key in enumerate(sorted(keys)):
        day = date.fromisoformat(key.split("/dt=")[1].split("/")[0])
        body = _get(s3, bucket, key)
        if day < first or body is None:
            continue
        path = into / f"{i}.parquet"
        path.write_bytes(body)
        files.append(path)
    return files


def _insert_files(con: Any, table: StateTable, files: Sequence[Path], target: str) -> None:
    listing = ", ".join(f"'{f}'" for f in files)
    cols = ", ".join(table.columns)
    con.execute(
        f"insert into {target} select {cols} from read_parquet([{listing}])"  # noqa: S608
    )


def load(s3: Any, bucket: str, db_path: str, table: StateTable, now: datetime) -> int:
    """Create `main.<name>` in the DuckDB file from the persisted rows; returns the row count.

    Never raises on a storage problem: a run that cannot read its state still builds (the table
    is left empty and the problem is logged), because models downstream read an empty table fine.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            files = fetch_files(s3, bucket, table, now, Path(tmp))
        except Exception:
            logger.exception("could not load state table %s; continuing with none", table.name)
            files = []
        con = duckdb.connect(db_path)
        try:
            con.execute("set TimeZone = 'UTC'")
            con.execute(table.ddl)
            if files:
                _insert_files(con, table, files, f"main.{table.name}")
            found = con.execute(f"select count(*) from main.{table.name}").fetchone()  # noqa: S608
        finally:
            con.close()
    return int(found[0]) if found else 0


def restore(s3: Any, bucket: str, db_path: str, table: StateTable, now: datetime) -> int:
    """Insert the persisted rows into ``main.<name>``, which a dbt model may already have created.

    `load` replaces the table; this is for tables whose DDL belongs to dbt (the Elementary result
    tables): the table is created from the declared columns only when it does not exist yet.
    Never raises on a storage problem, like `load`. Returns the rows now in the table.
    """
    with tempfile.TemporaryDirectory() as tmp:
        try:
            files = fetch_files(s3, bucket, table, now, Path(tmp))
        except Exception:
            logger.exception("could not restore state table %s; continuing with none", table.name)
            files = []
        con = duckdb.connect(db_path)
        try:
            con.execute("set TimeZone = 'UTC'")
            con.execute(table.ddl.replace("create or replace table", "create table if not exists"))
            if files:
                _insert_files(con, table, files, f"main.{table.name}")
            found = con.execute(f"select count(*) from main.{table.name}").fetchone()  # noqa: S608
        finally:
            con.close()
    return int(found[0]) if found else 0


def append(
    s3: Any, bucket: str, table: StateTable, new_rows: Sequence[tuple[Any, ...]], at: datetime
) -> str:
    """Add rows to the object of the UTC day of `at`; returns its key."""
    key = table.key(at.astimezone(UTC).date())
    with tempfile.TemporaryDirectory() as tmp:
        old = Path(tmp) / "old.parquet"
        out = Path(tmp) / "rows.parquet"
        existing = _get(s3, bucket, key)
        con = duckdb.connect()
        try:
            con.execute("set TimeZone = 'UTC'")
            con.execute(table.ddl.replace("main.", "", 1))
            if existing is not None:
                old.write_bytes(existing)
                _insert_files(con, table, [old], table.name)
            marks = ", ".join("?" for _ in table.columns)
            con.executemany(f"insert into {table.name} values ({marks})", list(new_rows))  # noqa: S608
            con.execute(f"copy {table.name} to '{out}' (format parquet)")
        finally:
            con.close()
        s3.put_object(Bucket=bucket, Key=key, Body=out.read_bytes(), ContentType=PARQUET)
    return key
