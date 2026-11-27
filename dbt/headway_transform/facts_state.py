"""Closed-day facts (ADR-024): the heavy facts of a service day are computed once and persisted.

``int_vehicle_stop_events``, ``fct_reconstructed_arrivals``, ``fct_headways`` and
``fct_stop_predictions`` dominate the transform and never change once their service day has
closed. Each closed, fully covered day of each is written once, as immutable Parquet, with the
history pattern (conditional put, never rewritten)::

    s3://<raw>/state/facts/<table>/metric_version=<N>/service_date=YYYY-MM-DD/part.parquet

Every run plans first: the window's service days that do NOT have all four partitions at the
current versions (open days and closed days not yet persisted, for instance after a missed run)
are ``compute`` days and are built from raw data; the others are ``persisted`` days and are read
from state. dbt unions the two (``fact_union``), so the window aggregates equal a full rebuild.
After the build the closed compute days are persisted. Nothing here decides which days are
closed except ``history.is_closed``, so a day is persisted by the first run after it closes and
by every later run until it is.
"""

import logging
import os
import tempfile
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from headway_transform import duckdb_limits, history, publish

logger = logging.getLogger()

PREFIX = "state/facts"
# table -> metric version. Bump a version when the definition of the table (or of anything it
# reads) changes: the new version is written next to the old one and `rebuild_facts` backfills it.
FACT_VERSIONS: dict[str, int] = {
    "int_vehicle_stop_events": 1,
    "fct_reconstructed_arrivals": 1,
    "fct_headways": 2,
    "fct_stop_predictions": 1,
}


def key_for(table: str, version: int, day: date) -> str:
    return f"{PREFIX}/{table}/metric_version={version}/service_date={day.isoformat()}/part.parquet"


def enabled() -> bool:
    """Release 2 only (the models are r2); HEADWAY_INCREMENTAL_FACTS=false is the kill switch."""
    return (
        publish.r2_enabled()
        and os.environ.get("HEADWAY_INCREMENTAL_FACTS", "true").lower() != "false"
    )


def window_days(now: datetime, lookback_days: int) -> list[date]:
    """Every service date the lookback window can hold, oldest first (one past today for safety)."""
    today = now.date()
    return [today - timedelta(days=i) for i in range(lookback_days, -2, -1)]


def _persisted_dates(s3: Any, bucket: str, table: str, version: int) -> set[date]:
    prefix = f"{PREFIX}/{table}/metric_version={version}/"
    found: set[date] = set()
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        for obj in page.get("Contents", []):
            rest = obj["Key"][len(prefix) :]
            head, _, tail = rest.partition("/")
            if head.startswith("service_date=") and tail == "part.parquet":
                found.add(date.fromisoformat(head.removeprefix("service_date=")))
        if not page.get("IsTruncated"):
            return found
        token = page["NextContinuationToken"]


@dataclass
class Plan:
    compute: list[date] = field(default_factory=list)
    persisted: list[date] = field(default_factory=list)

    def variables(self, root: str) -> dict[str, Any]:
        """The dbt vars of this plan (see macros/facts.sql)."""
        return {
            "compute_days": [d.isoformat() for d in self.compute],
            "persisted_days": [d.isoformat() for d in self.persisted],
            "facts_root": root,
            "fact_versions": dict(FACT_VERSIONS),
        }

    def summary(self) -> dict[str, list[str]]:
        return {
            "facts_computed_days": [d.isoformat() for d in self.compute],
            "facts_loaded_days": [d.isoformat() for d in self.persisted],
        }


def plan(
    s3: Any,
    bucket: str,
    now: datetime,
    lookback_days: int,
    tables: Mapping[str, int] | None = None,
) -> Plan:
    """Split the window: days with all tables persisted are loaded, every other day is computed."""
    versions = dict(tables or FACT_VERSIONS)
    present = [_persisted_dates(s3, bucket, t, v) for t, v in versions.items()]
    complete = set.intersection(*present) if present else set()
    window = window_days(now, lookback_days)
    result = Plan(
        compute=[d for d in window if d not in complete],
        persisted=[d for d in window if d in complete],
    )
    logger.info(
        "facts plan: compute %s, load from state %s",
        [d.isoformat() for d in result.compute],
        [d.isoformat() for d in result.persisted],
    )
    return result


@dataclass
class FactsResult:
    written: dict[str, list[str]] = field(default_factory=dict)  # table -> service dates
    existing: int = 0  # objects found already in place and left alone

    @property
    def n_written(self) -> int:
        return sum(len(v) for v in self.written.values())


def write_closed_days(
    s3: Any,
    bucket: str,
    db_path: str,
    now: datetime,
    *,
    timezone: str,
    lookback_days: int | None,
    skip: Collection[date] = (),
    days: Iterable[date] | None = None,
    tables: Mapping[str, int] | None = None,
) -> FactsResult:
    """Write each closed, fully covered service day that is not persisted yet.

    `skip` holds the days this run loaded from state (they are not written again); `days`
    limits the dates (a backfill); `tables` maps table to version (default: FACT_VERSIONS).
    Existing objects are never replaced, so two runs racing for a day write it once.
    """
    wanted = None if days is None else set(days)
    skipped = set(skip)
    result = FactsResult()
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        present = {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_schema = 'main'"
            ).fetchall()
        }
        for table, version in (tables or FACT_VERSIONS).items():
            if table not in present:
                logger.warning("facts: %s was not built, skipped", table)
                continue
            # Every day of the window, not only the days that have rows: a closed day can have
            # none in a fact (an outage day has no headways), and without a partition it would be
            # computed again by every run. An empty partition keeps the schema and says "done".
            dates = (
                window_days(now, lookback_days)
                if lookback_days is not None
                else [
                    r[0]
                    for r in con.execute(
                        f"select distinct service_date from main.{table} order by 1"  # noqa: S608
                    ).fetchall()
                ]
            )
            for day in dates:
                if day in skipped or (wanted is not None and day not in wanted):
                    continue
                if not (
                    history.is_closed(day, now, timezone)
                    and history.is_covered(day, now, lookback_days)
                ):
                    continue
                key = key_for(table, version, day)
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
        "facts: wrote %d day-tables %s, %d already present",
        result.n_written,
        {t: v for t, v in result.written.items()},
        result.existing,
    )
    return result


def validate_rebuild(event: Mapping[str, Any]) -> tuple[date, date, int]:
    """Check a ``rebuild_facts`` payload and return (from, to, metric_version)."""
    try:
        first = date.fromisoformat(str(event["from"]))
        last = date.fromisoformat(str(event["to"]))
        version = int(event["metric_version"])
    except (KeyError, ValueError) as exc:
        raise ValueError(
            'rebuild_facts needs {"from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "metric_version": N}'
        ) from exc
    if last < first:
        raise ValueError("rebuild_facts: 'to' is before 'from'")
    if (last - first).days + 1 > history.MAX_REBUILD_DAYS:
        raise ValueError(f"rebuild_facts covers at most {history.MAX_REBUILD_DAYS} days per call")
    if version not in FACT_VERSIONS.values():
        raise ValueError(
            f"metric_version {version} matches no table; the code builds {FACT_VERSIONS}. "
            "Bump the constant in facts_state.py to change a definition."
        )
    return first, last, version


def versions_for(version: int) -> dict[str, int]:
    """Tables currently at `version` (a backfill writes only those)."""
    return {t: v for t, v in FACT_VERSIONS.items() if v == version}
