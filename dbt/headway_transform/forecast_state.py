"""Closed-day training data for the weekly forecast job (ADR-019), kept in the raw bucket.

With HEADWAY_ENABLE_R3 the transform writes each CLOSED service day of two internal tables once,
as immutable Parquet, with the history pattern (conditional put, never rewritten)::

    s3://<raw>/state/forecast_examples/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet
    s3://<raw>/state/forecast_gate_days/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet

The training Lambda reads these partitions and never builds a model of its own, so its runtime does
not grow with the dbt build. A change of definition bumps the version here and writes next to the
old one; ``rebuild_forecast_examples`` backfills days the lookback has not covered.
"""

import logging
import tempfile
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from headway_transform import duckdb_limits, history

logger = logging.getLogger()

PREFIX = "state"
# table -> (state prefix, metric version). Bump a version when the table's definition changes.
TABLES: dict[str, tuple[str, int]] = {
    "fct_forecast_examples": ("forecast_examples", 1),
    "int_forecast_gate_days": ("forecast_gate_days", 1),
}


def key_for(name: str, version: int, day: date) -> str:
    return f"{PREFIX}/{name}/metric_version={version}/service_date={day.isoformat()}/part.parquet"


def versions() -> dict[str, int]:
    return {t: v for t, (_, v) in TABLES.items()}


def write_closed_days(
    s3: Any,
    bucket: str,
    db_path: str,
    now: datetime,
    *,
    timezone: str,
    lookback_days: int | None,
    days: Iterable[date] | None = None,
    only_version: int | None = None,
) -> history.HistoryResult:
    """Write each closed, fully covered service day that has no object yet. Existing objects are
    left alone. `days` limits the dates (a backfill); `only_version` the tables at that version."""
    wanted = None if days is None else set(days)
    result = history.HistoryResult()
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        present = {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema = 'main'"
            ).fetchall()
        }
        for table, (name, version) in TABLES.items():
            if table not in present or (only_version is not None and version != only_version):
                continue
            dates = [
                r[0]
                for r in con.execute(
                    f"select distinct service_date from main.{table} order by 1"  # noqa: S608
                ).fetchall()
            ]
            for day in dates:
                if wanted is not None and day not in wanted:
                    continue
                if not (
                    history.is_closed(day, now, timezone)
                    and history.is_covered(day, now, lookback_days)
                ):
                    continue
                key = key_for(name, version, day)
                if history._exists(s3, bucket, key):  # noqa: SLF001
                    result.existing += 1
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "part.parquet"
                    con.execute(
                        f"copy (select * from main.{table} where service_date = ?) "  # noqa: S608
                        f"to '{path}' (format parquet)",
                        [day],
                    )
                    created = history._put_once(s3, bucket, key, path)  # noqa: SLF001
                if created:
                    result.written.setdefault(table, []).append(day.isoformat())
                else:
                    result.existing += 1
    finally:
        con.close()
    logger.info(
        "forecast state: wrote %d day-tables, %d already present", result.n_written, result.existing
    )
    return result


def validate_rebuild(event: Mapping[str, Any]) -> tuple[date, date, int]:
    """Check a ``rebuild_forecast_examples`` payload and return (from, to, version)."""
    try:
        first = date.fromisoformat(str(event["from"]))
        last = date.fromisoformat(str(event["to"]))
        version = int(event["metric_version"])
    except (KeyError, ValueError) as exc:
        raise ValueError(
            'rebuild_forecast_examples needs {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", '
            '"metric_version": N}'
        ) from exc
    if last < first:
        raise ValueError("rebuild_forecast_examples: 'to' is before 'from'")
    if (last - first).days + 1 > history.MAX_REBUILD_DAYS:
        raise ValueError(
            f"rebuild_forecast_examples covers at most {history.MAX_REBUILD_DAYS} days per call"
        )
    if version not in versions().values():
        raise ValueError(
            f"metric_version {version} matches no table; the code builds {versions()}. "
            "Bump the constant in forecast_state.py to change a definition."
        )
    return first, last, version
