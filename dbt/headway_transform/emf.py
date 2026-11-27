"""CloudWatch Embedded Metric Format lines from a transform run (namespace ``Headway``).

Like ingest, the transform writes bare JSON lines to stdout and CloudWatch Logs turns them into
metrics, so the function needs no ``PutMetricData`` permission. Three metrics, each computed from
the local DuckDB file after the build:

- ``GoldAgeMinutes``: how old the previous gold export was when this run replaced it. The first run
  after a deploy has no previous export and emits nothing; the alarm keeps its state on missing
  data, and a transform that stops is the job of the transform-not-succeeded alarm.
- ``MissedRunsLastHour``: minutes without an ingest manifest in the 60 minutes ending at the newest
  curated minute (curation lags ingest, so the window follows the data, not the clock).
- ``ArrivalCoveragePct`` (Release 2): share of the scheduled stop times of the last complete local
  hour that have a usable (high or medium confidence) reconstructed arrival. Emitted only in
  service hours (05:00 to 01:00 local); outside them there is nothing to measure.
"""

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from headway_transform import duckdb_limits

logger = logging.getLogger()
NAMESPACE = "Headway"
SERVICE_START_HOUR = 5
SERVICE_END_HOUR = 1  # service runs until 01:00 local


def emf_line(timestamp: datetime, metrics: Mapping[str, float], units: Mapping[str, str]) -> str:
    return json.dumps(
        {
            "_aws": {
                "Timestamp": int(timestamp.timestamp() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": NAMESPACE,
                        "Dimensions": [[]],
                        "Metrics": [{"Name": n, "Unit": units.get(n, "Count")} for n in metrics],
                    }
                ],
            },
            **metrics,
        },
        separators=(",", ":"),
    )


def emit(timestamp: datetime, metrics: Mapping[str, float]) -> list[str]:
    """Print one EMF line per metric (own period each: they are alarmed separately)."""
    units = {"GoldAgeMinutes": "None", "ArrivalCoveragePct": "Percent"}
    lines = [emf_line(timestamp, {name: value}, units) for name, value in metrics.items()]
    for line in lines:
        print(line, flush=True)  # noqa: T201
    return lines


def previous_export(db_path: str) -> datetime | None:
    """Newest ``gold_exported_at`` in the loaded run history (before this run's own record)."""
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        row = con.execute(
            "select epoch(max(gold_exported_at)) from main.agg_transform_runs "
            "where status <> 'failed'"
        ).fetchone()
    except duckdb.Error:
        return None
    finally:
        con.close()
    # epoch seconds: reading a timestamptz into Python would need pytz, which the image lacks
    return datetime.fromtimestamp(row[0], UTC) if row and row[0] is not None else None


def gold_age_minutes(previous: datetime | None, exported_at: datetime) -> float | None:
    if previous is None:
        return None
    return round(max((exported_at - previous).total_seconds(), 0) / 60, 1)


def missed_runs_last_hour(db_path: str) -> int | None:
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        row = con.execute(
            """
            with last as (select date_trunc('minute', max(scheduled_at)) as m from stg_manifests)
            select
                60 - count(distinct date_trunc('minute', s.scheduled_at)),
                any_value(last.m is not null)
            from stg_manifests s, last
            where s.scheduled_at > last.m - interval 60 minute
                and s.scheduled_at < last.m + interval 1 minute
            """
        ).fetchone()
    finally:
        con.close()
    return max(int(row[0]), 0) if row and row[1] is not None else None


def in_service_hours(local_hour: int) -> bool:
    return local_hour >= SERVICE_START_HOUR or local_hour < SERVICE_END_HOUR


def arrival_coverage_pct(
    db_path: str, now: datetime, lag: timedelta, timezone: str
) -> float | None:
    """Coverage of the last complete local hour; None outside service hours or with no schedule."""
    local_end = (
        (now - lag).astimezone(ZoneInfo(timezone)).replace(minute=0, second=0, microsecond=0)
    )
    start = (local_end - timedelta(hours=1)).astimezone(UTC)
    if not in_service_hours((local_end - timedelta(hours=1)).hour):
        return None
    end = local_end.astimezone(UTC)
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        row = con.execute(
            """
            select
                (select count(*) from int_scheduled_stop_times
                  where epoch(scheduled_arrival_ts) >= ? and epoch(scheduled_arrival_ts) < ?),
                (select count(*) from int_usable_arrivals
                  where is_nearest_segment
                    and epoch(scheduled_arrival_ts) >= ? and epoch(scheduled_arrival_ts) < ?)
            """,
            [start.timestamp(), end.timestamp()] * 2,
        ).fetchone()
    finally:
        con.close()
    if not row or not row[0]:
        return None
    return float(round(min(100.0 * row[1] / row[0], 100.0), 1))


def collect(
    db_path: str,
    now: datetime,
    *,
    previous: datetime | None,
    exported_at: datetime,
    r2: bool,
    lag: timedelta,
    timezone: str,
) -> dict[str, float]:
    """The metrics of a finished run; one that cannot be computed is left out and logged."""
    metrics: dict[str, float] = {}
    jobs: dict[str, Any] = {
        "GoldAgeMinutes": lambda: gold_age_minutes(previous, exported_at),
        "MissedRunsLastHour": lambda: missed_runs_last_hour(db_path),
    }
    if r2:
        jobs["ArrivalCoveragePct"] = lambda: arrival_coverage_pct(db_path, now, lag, timezone)
    for name, job in jobs.items():
        try:
            value = job()
        except Exception:
            logger.exception("metric %s could not be computed", name)
            continue
        if value is not None:
            metrics[name] = value
    return metrics


def emit_memory(timestamp: datetime, used_pct: float) -> str:
    """Print the run's peak memory as a percentage of the function's limit (alarmed at 85%)."""
    line = emf_line(
        timestamp, {"TransformMemoryUsedPct": used_pct}, {"TransformMemoryUsedPct": "Percent"}
    )
    print(line, flush=True)  # noqa: T201
    return line


def emit_data_tests(timestamp: datetime, failed: int, warned: int) -> list[str]:
    """Print the run's data-test counts: ``DataTestsFailed`` (error severity, alarmed at 1 or more)
    and ``DataTestsWarned`` (warn severity, summary only). Zero is emitted on a clean run so the
    alarm clears by itself."""
    lines = [
        emf_line(timestamp, {"DataTestsFailed": failed}, {}),
        emf_line(timestamp, {"DataTestsWarned": warned}, {}),
    ]
    for line in lines:
        print(line, flush=True)  # noqa: T201
    return lines


def emit_duration(timestamp: datetime, seconds: float) -> str:
    """Print the run's wall time (alarmed above 600 s twice in a row; the Lambda limit is 900 s)."""
    line = emf_line(
        timestamp,
        {"TransformDurationSeconds": round(seconds, 1)},
        {"TransformDurationSeconds": "Seconds"},
    )
    print(line, flush=True)  # noqa: T201
    return line
