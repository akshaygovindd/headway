"""Immutable history of daily aggregates: closing rule, one write per day, versions, dbt union."""

import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from headway_transform import history

TABLE = "agg_prediction_accuracy_daily"
KEY = (
    "public/boston/history/table=agg_prediction_accuracy_daily/"
    "metric_version=1/service_date={}/part.parquet"
)


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "t",
        "AWS_SECRET_ACCESS_KEY": "t",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="pub")
        yield client


def _db(path: Path, rows: list[tuple[str, int]], table: str = TABLE) -> str:
    con = duckdb.connect(str(path))
    con.execute(f"create table {table} (service_date date, line varchar, n bigint)")
    con.executemany(f"insert into {table} values (?, 'Red', ?)", rows)
    con.close()
    return str(path)


def _write(s3: Any, db: str, now: datetime, **kw: Any) -> history.HistoryResult:
    return history.write_closed_days(
        s3, "pub", "boston", db, now, timezone="America/New_York", lookback_days=7,
        tables={TABLE: 1}, **kw,
    )  # fmt: skip


# ---------------------------------------------------------------- closing rule


def test_a_day_closes_at_six_local_on_the_next_day() -> None:
    day = date(2026, 11, 22)
    assert history.closes_at(day, "America/New_York") == datetime(2026, 11, 23, 11, 0, tzinfo=UTC)
    tz = "America/New_York"
    assert not history.is_closed(day, datetime(2026, 11, 23, 10, 59, tzinfo=UTC), tz)
    assert history.is_closed(day, datetime(2026, 11, 23, 11, 0, tzinfo=UTC), tz)


def test_closing_follows_daylight_saving_time() -> None:
    ny = "America/New_York"
    # 2026-11-01: the clocks go back at 02:00, so that morning's 06:00 is already EST (UTC-5)
    assert history.closes_at(date(2026, 10, 31), ny) == datetime(2026, 11, 1, 11, 0, tzinfo=UTC)
    assert history.closes_at(date(2026, 11, 1), ny) == datetime(2026, 11, 2, 11, 0, tzinfo=UTC)
    # 2026-03-08: the clocks go forward at 02:00, so the 06:00 that follows is EDT (UTC-4)
    assert history.closes_at(date(2026, 3, 7), ny) == datetime(2026, 3, 8, 10, 0, tzinfo=UTC)
    assert history.closes_at(date(2026, 3, 8), ny) == datetime(2026, 3, 9, 10, 0, tzinfo=UTC)


def test_the_oldest_day_of_the_lookback_window_is_not_complete() -> None:
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    assert history.is_covered(date(2026, 11, 16), now, 7)
    assert not history.is_covered(date(2026, 11, 15), now, 7)
    assert history.is_covered(date(2000, 1, 1), now, None)


# ---------------------------------------------------------------- writing


def test_only_closed_days_are_written_once_each(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "a.duckdb", [("2026-11-21", 5), ("2026-11-22", 7), ("2026-11-23", 9)])
    now = datetime(
        2026, 11, 23, 12, 0, tzinfo=UTC
    )  # the 22nd closed at 11:00 UTC; the 23rd is open
    result = _write(s3, db, now)
    assert result.written == {TABLE: ["2026-11-21", "2026-11-22"]}
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket="pub")["Contents"])
    assert keys == [KEY.format("2026-11-21"), KEY.format("2026-11-22")]
    body = s3.get_object(Bucket="pub", Key=KEY.format("2026-11-22"))["Body"].read()
    path = tmp_path / "x.parquet"
    path.write_bytes(body)
    assert pq.read_table(path).to_pylist() == [
        {"service_date": date(2026, 11, 22), "line": "Red", "n": 7}
    ]


def test_a_written_day_is_never_rewritten(s3: Any, tmp_path: Path) -> None:
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    _write(s3, _db(tmp_path / "a.duckdb", [("2026-11-22", 7)]), now)
    etag = s3.head_object(Bucket="pub", Key=KEY.format("2026-11-22"))["ETag"]
    # the same day now computes to a different value (a late correction): history keeps the first
    again = _write(s3, _db(tmp_path / "b.duckdb", [("2026-11-22", 999)]), now)
    assert again.n_written == 0 and again.existing == 1
    assert s3.head_object(Bucket="pub", Key=KEY.format("2026-11-22"))["ETag"] == etag


def test_losing_the_conditional_put_leaves_the_winner_in_place(s3: Any, tmp_path: Path) -> None:
    """Two runs both find the day missing; the second put is refused and counted as existing."""
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    db = _db(tmp_path / "a.duckdb", [("2026-11-22", 7)])
    real_exists = history._exists

    def racing_exists(client: Any, bucket: str, key: str) -> bool:
        found = real_exists(client, bucket, key)
        client.put_object(Bucket=bucket, Key=key, Body=b"winner")  # the other run lands first
        return found

    history._exists = racing_exists  # type: ignore[assignment]
    try:
        result = _write(s3, db, now)
    finally:
        history._exists = real_exists  # type: ignore[assignment]
    assert result.n_written == 0 and result.existing == 1
    assert s3.get_object(Bucket="pub", Key=KEY.format("2026-11-22"))["Body"].read() == b"winner"


def test_metric_versions_are_stored_side_by_side(s3: Any, tmp_path: Path) -> None:
    now = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
    db = _db(tmp_path / "a.duckdb", [("2026-11-22", 7)])
    _write(s3, db, now)
    history.write_closed_days(
        s3,
        "pub",
        "boston",
        db,
        now,
        timezone="America/New_York",
        lookback_days=7,
        tables={TABLE: 2},
    )
    keys = sorted(o["Key"] for o in s3.list_objects_v2(Bucket="pub")["Contents"])
    assert keys == [
        KEY.format("2026-11-22"),
        KEY.format("2026-11-22").replace("metric_version=1", "metric_version=2"),
    ]


def test_a_backfill_writes_only_the_requested_days(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "a.duckdb", [("2026-11-20", 1), ("2026-11-21", 2), ("2026-11-22", 3)])
    result = _write(s3, db, datetime(2026, 11, 23, 12, 0, tzinfo=UTC), days=[date(2026, 11, 21)])
    assert result.written == {TABLE: ["2026-11-21"]}


def test_the_family_constants_cover_every_history_table() -> None:
    assert set(history.HISTORY_TABLES.values()) == set(history.METRIC_VERSIONS)
    assert history.table_versions()[TABLE] == history.METRIC_VERSIONS["prediction_accuracy"]


# ---------------------------------------------------------------- rebuild payload


def test_rebuild_payload_validation() -> None:
    ok = {"from": "2026-11-10", "to": "2026-11-12", "metric_version": 1}
    assert history.validate_rebuild(ok) == (date(2026, 11, 10), date(2026, 11, 12), 1)
    for bad, text in [
        ({**ok, "to": "2026-11-09"}, "before"),
        ({**ok, "to": "2026-12-30"}, "at most 14 days"),
        ({**ok, "metric_version": 7}, "matches no family"),
        ({"from": "2026-11-10"}, "needs"),
    ]:
        with pytest.raises(ValueError, match=text):
            history.validate_rebuild(bad)


def test_a_backfill_writes_only_tables_at_the_requested_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(history.METRIC_VERSIONS, "stop_deviation", 4)
    assert history.versions_for(4) == {"agg_stop_deviation_daily": 4}
    assert "agg_stop_deviation_daily" not in history.versions_for(history.ARRIVAL_TIMES_VERSION)


# ---------------------------------------------------------------- dbt union


ROOT = Path(__file__).resolve().parents[2]


def _show(sql: str, tmp_path: Path, vars_json: str) -> list[dict[str, Any]]:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run dbt")
    proc = subprocess.run(
        [
            "uv", "run", "--project", "dbt", "dbt", "show", "--quiet",
            "--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local",
            "--output", "json", "--limit", "100", "--vars", vars_json, "--inline", sql,
        ],  # fmt: skip
        cwd=ROOT, env={**os.environ, "HEADWAY_DUCKDB_PATH": str(tmp_path / "m.duckdb")},
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)["show"]  # type: ignore[no-any-return]


COMPUTED = (
    "with computed as (select date '2026-11-21' as service_date, 'computed' as src "
    "union all select date '2026-11-22', 'computed' "
    "union all select date '2026-11-23', 'computed') "
    "{{ history_union('agg_prediction_accuracy_daily') }} order by 1"
)


def test_dbt_takes_closed_days_from_history_and_open_days_from_the_build(tmp_path: Path) -> None:
    part = tmp_path / "hist" / "table=agg_prediction_accuracy_daily" / "metric_version=1"
    old = part / "service_date=2026-11-20"
    old.mkdir(parents=True)
    closed = part / "service_date=2026-11-22"
    closed.mkdir()
    con = duckdb.connect()
    for folder, day in ((old, "2026-11-20"), (closed, "2026-11-22")):
        con.execute(
            f"copy (select date '{day}' as service_date, 'history' as src) "
            f"to '{folder}/part.parquet'"
        )
    # a different metric version is invisible
    other = (
        tmp_path
        / "hist"
        / "table=agg_prediction_accuracy_daily"
        / "metric_version=2"
        / "service_date=2026-11-21"
    )
    other.mkdir(parents=True)
    con.execute(
        f"copy (select date '2026-11-21' as service_date, 'v2' as src) to '{other}/part.parquet'"
    )
    vars_json = json.dumps({
        "history_root": str(tmp_path / "hist"),
        "metric_versions": {"agg_prediction_accuracy_daily": 1},
    })  # fmt: skip
    rows = _show(COMPUTED, tmp_path, vars_json)
    assert [(r["service_date"], r["src"]) for r in rows] == [
        ("2026-11-20", "history"),
        ("2026-11-21", "computed"),
        ("2026-11-22", "history"),
        ("2026-11-23", "computed"),
    ]


def test_dbt_union_is_a_no_op_without_history(tmp_path: Path) -> None:
    vars_json = json.dumps({
        "history_root": str(tmp_path / "empty"),
        "metric_versions": {"agg_prediction_accuracy_daily": 1},
    })  # fmt: skip
    assert len(_show(COMPUTED, tmp_path, vars_json)) == 3
    assert len(_show(COMPUTED, tmp_path, "{}")) == 3  # flag off: no vars at all


def test_a_table_that_was_not_built_is_skipped(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "a.duckdb", [("2026-11-22", 7)])
    result = history.write_closed_days(
        s3, "pub", "boston", db, datetime(2026, 11, 23, 12, 0, tzinfo=UTC),
        timezone="America/New_York", lookback_days=7,
        tables={TABLE: 1, "agg_ett_daily": 1},
    )  # fmt: skip
    assert result.written == {TABLE: ["2026-11-22"]}


def test_publish_adds_history_tables_that_have_objects() -> None:
    from headway_transform import publish

    class Con:
        def __init__(self) -> None:
            self.sql: list[str] = []
            self.found = 0

        def execute(self, sql: str) -> "Con":
            self.sql.append(sql)
            self.found = 0 if "agg_ett_daily" in sql else 3
            return self

        def fetchone(self) -> tuple[int]:
            return (self.found,)

        def close(self) -> None:
            pass

    con = Con()
    out = publish.publish_motherduck(
        "tok", "s3://b/public/boston/gold", ["dim_route"], connect=lambda t: con,
        history_url="s3://b/public/boston/history",
        history_tables=["agg_prediction_accuracy_daily", "agg_ett_daily"],
    )  # fmt: skip
    assert out == ["dim_route", "history_agg_prediction_accuracy_daily"]
    created = [s for s in con.sql if "history_" in s and s.startswith("create or replace")]
    assert len(created) == 1
    assert "metric_version=*/service_date=*/part.parquet" in created[0]
    assert "hive_partitioning = true" in created[0]
