"""Daily AWS cost report (Cost Explorer) written as gold/cost_daily.parquet."""

import tempfile
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

COST_KEY_NAME = "cost_daily.parquet"
RUN_AFTER_HOUR_UTC = 6
LOOKBACK_DAYS = 14
TAG_KEY = "project"
TAG_VALUE = "headway"

_COLUMNS = (
    "date DATE, service VARCHAR, cost_usd DOUBLE, day_total_usd DOUBLE, "
    "captures BIGINT, cost_per_1000_captures DOUBLE"
)


def due(last_modified: datetime | None, now: datetime) -> bool:
    """Run once per UTC day, on the first transform run at or after 06:00 UTC."""
    now = now.astimezone(UTC)
    if now.hour < RUN_AFTER_HOUR_UTC:
        return False
    return last_modified is None or last_modified.astimezone(UTC).date() < now.date()


def fetch_costs(ce: Any, today: date) -> dict[date, dict[str, float]]:
    """Unblended cost per day and service for the tagged project; complete days only."""
    start = today - timedelta(days=LOOKBACK_DAYS)
    out: dict[date, dict[str, float]] = {}
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "TimePeriod": {"Start": start.isoformat(), "End": today.isoformat()},
            "Granularity": "DAILY",
            "Metrics": ["UnblendedCost"],
            "GroupBy": [{"Type": "DIMENSION", "Key": "SERVICE"}],
            "Filter": {"Tags": {"Key": TAG_KEY, "Values": [TAG_VALUE]}},
        }
        if token:
            kwargs["NextPageToken"] = token
        response = ce.get_cost_and_usage(**kwargs)
        for period in response.get("ResultsByTime", []):
            day = date.fromisoformat(period["TimePeriod"]["Start"])
            services = out.setdefault(day, {})
            for group in period.get("Groups", []):
                amount = float(group["Metrics"]["UnblendedCost"]["Amount"])
                services[group["Keys"][0]] = services.get(group["Keys"][0], 0.0) + amount
        token = response.get("NextPageToken")
        if not token:
            return out


def captures_by_day(db_path: str) -> dict[date, int]:
    """Captures per UTC day from the dbt pipeline-health table."""
    con = duckdb.connect(db_path, read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        rows = con.execute(
            "select cast(scheduled_at as date), sum(n_captures)::bigint "
            "from main.agg_pipeline_health group by 1"
        ).fetchall()
    finally:
        con.close()
    return {d: int(n) for d, n in rows}


def build_rows(
    costs: Mapping[date, Mapping[str, float]], captures: Mapping[date, int]
) -> list[tuple[date, str, float, float, int | None, float | None]]:
    rows: list[tuple[date, str, float, float, int | None, float | None]] = []
    for day in sorted(costs):
        total = sum(costs[day].values())
        n = captures.get(day)
        per_1000 = total / n * 1000 if n else None
        for service in sorted(costs[day]):
            rows.append((day, service, costs[day][service], total, n, per_1000))
    return rows


def write_parquet(rows: list[tuple[Any, ...]]) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / COST_KEY_NAME
        con = duckdb.connect()
        con.execute(f"create table cost ({_COLUMNS})")
        if rows:
            con.executemany("insert into cost values (?, ?, ?, ?, ?, ?)", rows)
        con.execute(f"copy cost to '{path}' (format parquet)")
        con.close()
        return path.read_bytes()


def write_cost_daily(
    s3: Any, bucket: str, gold_prefix: str, ce: Any, db_path: str, now: datetime
) -> int | None:
    """Write cost_daily.parquet if due; returns the row count, or None when not due."""
    key = f"{gold_prefix}/{COST_KEY_NAME}"
    try:
        last_modified: datetime | None = s3.head_object(Bucket=bucket, Key=key)["LastModified"]
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
            raise
        last_modified = None
    if not due(last_modified, now):
        return None
    costs = fetch_costs(ce, now.astimezone(UTC).date())
    rows = build_rows(costs, captures_by_day(db_path))
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=write_parquet(rows),
        ContentType="application/vnd.apache.parquet",
        CacheControl="max-age=300",
    )
    return len(rows)
