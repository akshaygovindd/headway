"""Immutable history of daily aggregates (ADR-015), written once a service day has closed.

A service day closes at 06:00 local on the day after its service date: the day itself ends at
03:00, and three hours of margin let curation catch up. Each closed day of each daily aggregate is
written once, as one Parquet object::

    s3://<public>/public/<city>/history/table=<agg>/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet

An object is created with ``If-None-Match: *`` and is never rewritten. A change of definition
bumps the metric version of its family (``METRIC_VERSIONS``, the only place versions live), which
writes next to the old version instead of over it; ``rebuild_history`` backfills it.
"""

import contextlib
import logging
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from headway_transform import duckdb_limits

logger = logging.getLogger()

CLOSE_AFTER_LOCAL = time(6, 0)
MAX_REBUILD_DAYS = 14
_CONFLICT = {"PreconditionFailed", "ConditionalRequestConflict"}

# Metric families and their version. Bump a version when the definition of any aggregate of the
# family changes; never edit history in place.
# Version 2 of the families that carry the service-disruption label (and the SLOs): an alert
# disrupts a line only when it covers the route or two or more stations (docs/METRICS.md).
# Version 3 of every family whose values read reconstructed arrival times, directly or through
# int_usable_arrivals (fct_reconstructed_arrivals, fct_headways): bracketed arrivals are corrected
# for the dwell the polls straddled (ADR-026), so closed days are rebuilt with one method instead
# of mixing the old and the new. They share one version number so a single `rebuild_history` call
# with metric_version 3 rebuilds them all; tests/unit/test_history_lineage.py checks that every
# family that depends on those facts is at this version.
ARRIVAL_TIMES_VERSION = 3
METRIC_VERSIONS: dict[str, int] = {
    "arrival_reconstruction": ARRIVAL_TIMES_VERSION,
    "prediction_accuracy": ARRIVAL_TIMES_VERSION,
    "actual_deviation": ARRIVAL_TIMES_VERSION,
    "headway_adherence": ARRIVAL_TIMES_VERSION,
    "ett": ARRIVAL_TIMES_VERSION,
    "stop_deviation": 1,
    "slo": ARRIVAL_TIMES_VERSION,
    "countdown_accuracy": ARRIVAL_TIMES_VERSION,
}
# Daily aggregate -> metric family.
HISTORY_TABLES: dict[str, str] = {
    "agg_arrival_reconstruction_daily": "arrival_reconstruction",
    "agg_prediction_accuracy_daily": "prediction_accuracy",
    "agg_actual_deviation_daily": "actual_deviation",
    # Hourly medians, for the Release 3 incident baseline (day type x hour over >= 14 days).
    "agg_actual_deviation_hourly": "actual_deviation",
    "agg_headway_adherence_daily": "headway_adherence",
    "agg_ett_daily": "ett",
    "agg_stop_deviation_daily": "stop_deviation",
    "agg_slo_daily": "slo",
    "agg_countdown_accuracy_daily": "countdown_accuracy",
}


def version_of(table: str) -> int:
    return METRIC_VERSIONS[HISTORY_TABLES[table]]


def table_versions() -> dict[str, int]:
    """Metric version per history table, the ``metric_versions`` dbt var."""
    return {t: version_of(t) for t in HISTORY_TABLES}


def history_prefix(city: str) -> str:
    return f"public/{city}/history"


def history_key(prefix: str, table: str, version: int, day: date) -> str:
    partition = f"metric_version={version}/service_date={day.isoformat()}"
    return f"{prefix}/table={table}/{partition}/part.parquet"


def closes_at(day: date, timezone: str) -> datetime:
    """The instant (UTC) a service day closes: 06:00 local on the next calendar day."""
    local = datetime.combine(day + timedelta(days=1), CLOSE_AFTER_LOCAL, tzinfo=ZoneInfo(timezone))
    return local.astimezone(UTC)


def is_closed(day: date, now: datetime, timezone: str) -> bool:
    return now >= closes_at(day, timezone)


def is_covered(day: date, now: datetime, lookback_days: int | None) -> bool:
    """True when the realtime lookback window holds every partition of the service day.

    A service day starts at 03:00 local, which falls on the UTC date of the service date, so the
    day is complete when that date is within `lookback_days` of today (UTC); the oldest day of
    the window may be cut. No lookback (local builds) covers everything.
    """
    return lookback_days is None or day >= now.astimezone(UTC).date() - timedelta(
        days=lookback_days
    )


@dataclass
class HistoryResult:
    written: dict[str, list[str]] = field(default_factory=dict)  # table -> service dates
    existing: int = 0  # objects found already in place and left alone

    @property
    def n_written(self) -> int:
        return sum(len(v) for v in self.written.values())


def _exists(s3: Any, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    return True


def _put_once(s3: Any, bucket: str, key: str, body: bytes | Path) -> bool:
    """Create the object unless it exists (conditional put); True when this call created it.

    A ``Path`` is streamed from disk rather than read whole into memory (ADR-024).
    """
    try:
        with contextlib.ExitStack() as stack:
            data = stack.enter_context(body.open("rb")) if isinstance(body, Path) else body
            s3.put_object(
                Bucket=bucket, Key=key, Body=data, IfNoneMatch="*",
                ContentType="application/vnd.apache.parquet",
                CacheControl="max-age=31536000, immutable",
            )  # fmt: skip
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] in _CONFLICT:
            return False
        raise
    return True


def write_closed_days(
    s3: Any,
    bucket: str,
    city: str,
    db_path: str,
    now: datetime,
    *,
    timezone: str,
    lookback_days: int | None,
    days: Iterable[date] | None = None,
    tables: Mapping[str, int] | None = None,
) -> HistoryResult:
    """Write each closed, fully covered service day of each daily aggregate that has no object yet.

    Reads the tables dbt built in `db_path` (close dbt's connection first). `days` limits the
    service dates (a backfill); `tables` maps table to metric version (default: the constants).
    Existing objects are never replaced.
    """
    prefix = history_prefix(city)
    wanted = None if days is None else set(days)
    result = HistoryResult()
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        present = {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema = 'main'"
            ).fetchall()
        }
        for table, version in (tables or table_versions()).items():
            if table not in present:
                logger.warning("history: %s was not built, skipped", table)
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
                if not (is_closed(day, now, timezone) and is_covered(day, now, lookback_days)):
                    continue
                key = history_key(prefix, table, version, day)
                if _exists(s3, bucket, key):
                    result.existing += 1
                    continue
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "part.parquet"
                    con.execute(
                        f"copy (select * from main.{table} where service_date = ?) "  # noqa: S608
                        f"to '{path}' (format parquet)",
                        [day],
                    )
                    created = _put_once(s3, bucket, key, path)
                if created:
                    result.written.setdefault(table, []).append(day.isoformat())
                else:
                    result.existing += 1
    finally:
        con.close()
    logger.info(
        "history: wrote %d day-tables, %d already present", result.n_written, result.existing
    )
    return result


def validate_rebuild(event: Mapping[str, Any]) -> tuple[date, date, int]:
    """Check a ``rebuild_history`` payload and return (from, to, metric_version)."""
    try:
        first = date.fromisoformat(str(event["from"]))
        last = date.fromisoformat(str(event["to"]))
        version = int(event["metric_version"])
    except (KeyError, ValueError) as exc:
        raise ValueError(
            'rebuild_history needs {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "metric_version": N}'
        ) from exc
    if last < first:
        raise ValueError("rebuild_history: 'to' is before 'from'")
    if (last - first).days + 1 > MAX_REBUILD_DAYS:
        raise ValueError(f"rebuild_history covers at most {MAX_REBUILD_DAYS} days per call")
    stale = sorted({f for f, v in METRIC_VERSIONS.items() if v != version})
    if len(stale) == len(METRIC_VERSIONS):
        raise ValueError(
            f"metric_version {version} matches no family; the code builds {METRIC_VERSIONS}. "
            "Bump the constant in history.py to change a definition."
        )
    return first, last, version


def versions_for(version: int) -> dict[str, int]:
    """Tables whose family is currently at `version` (a backfill writes only those)."""
    return {t: v for t, v in table_versions().items() if v == version}


def history_enabled() -> bool:
    return os.environ.get("HEADWAY_ENABLE_R2", "").lower() == "true"
