"""Persisted run state: tables kept as Parquet in the raw bucket and loaded at startup."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pytest
from moto import mock_aws

from headway_transform import state

NOW = datetime(2026, 11, 23, 12, 20, 5, tzinfo=UTC)


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
        client.create_bucket(Bucket="raw")
        yield client


def _run(run_id: str, at: datetime, status: str = "success") -> tuple[Any, ...]:
    return (
        run_id, "scheduled", state.slot_of(at), at, at + timedelta(seconds=90), status, 10, 0,
        1234, at + timedelta(seconds=80), True, True, 90_000, "{}", None,
    )  # fmt: skip


def _table(db: str) -> list[tuple[Any, ...]]:
    con = duckdb.connect(db, read_only=True)
    con.execute("set TimeZone = 'UTC'")
    rows = con.execute(
        "select run_id, status, strftime(slot, '%Y-%m-%dT%H:%M:%S') "
        "from main.agg_transform_runs order by started_at"
    ).fetchall()
    con.close()
    return rows  # type: ignore[no-any-return]


def test_slot_is_the_scheduled_hour() -> None:
    assert state.slot_of(datetime(2026, 11, 23, 12, 20, 5, tzinfo=UTC)) == datetime(
        2026, 11, 23, 12, 0, tzinfo=UTC
    )
    assert state.slot_of(datetime(2026, 11, 23, 12, 19, 59, tzinfo=UTC)) == datetime(
        2026, 11, 23, 11, 0, tzinfo=UTC
    )
    assert state.slot_of(datetime(2026, 11, 23, 12, 50, tzinfo=UTC)) == datetime(
        2026, 11, 23, 12, 0, tzinfo=UTC
    )


def test_rows_survive_between_runs(s3: Any, tmp_path: Path) -> None:
    first = NOW
    state.append(s3, "raw", state.TRANSFORM_RUNS, [_run("a", first)], first)
    second = NOW + timedelta(hours=1)
    key = state.append(s3, "raw", state.TRANSFORM_RUNS, [_run("b", second, "failed")], second)
    assert key == "state/transform_runs/dt=2026-11-23/rows.parquet"  # one object per UTC day

    db = str(tmp_path / "fresh-container.duckdb")  # a new /tmp: nothing but S3 remembers
    assert state.load(s3, "raw", db, state.TRANSFORM_RUNS, second) == 2
    assert [(r[0], r[1]) for r in _table(db)] == [("a", "success"), ("b", "failed")]
    assert _table(db)[0][2] == "2026-11-23T12:00:00"  # the 12:20 run belongs to the 12:00 slot


def test_load_is_bounded_to_the_retention_window(s3: Any, tmp_path: Path) -> None:
    old = NOW - timedelta(days=40)
    state.append(s3, "raw", state.TRANSFORM_RUNS, [_run("old", old)], old)
    recent = NOW - timedelta(days=34)
    state.append(s3, "raw", state.TRANSFORM_RUNS, [_run("recent", recent)], recent)
    db = str(tmp_path / "a.duckdb")
    state.load(s3, "raw", db, state.TRANSFORM_RUNS, NOW)
    assert [r[0] for r in _table(db)] == ["recent"]


def test_a_missing_or_unreadable_state_gives_an_empty_table(
    s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "a.duckdb")
    assert state.load(s3, "raw", db, state.TRANSFORM_RUNS, NOW) == 0
    assert _table(db) == []

    def boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError("s3 down")

    monkeypatch.setattr(state, "fetch_files", boom)
    assert state.load(s3, "raw", db, state.TRANSFORM_RUNS, NOW) == 0  # logged, not raised


def test_loading_again_replaces_the_table(s3: Any, tmp_path: Path) -> None:
    state.append(s3, "raw", state.TRANSFORM_RUNS, [_run("a", NOW)], NOW)
    db = str(tmp_path / "warm.duckdb")
    state.load(s3, "raw", db, state.TRANSFORM_RUNS, NOW)
    state.load(s3, "raw", db, state.TRANSFORM_RUNS, NOW)  # a warm container loads again
    assert len(_table(db)) == 1


def test_another_table_can_use_the_same_mechanism(s3: Any, tmp_path: Path) -> None:
    """The Elementary result tables (2.5) declare a StateTable and call the same functions."""
    results = state.StateTable(
        name="elementary_test_results", folder="elementary/test_results", timestamp="detected_at",
        columns={"test_id": "varchar", "status": "varchar", "detected_at": "timestamptz"},
    )  # fmt: skip
    state.append(s3, "raw", results, [("t1", "pass", NOW), ("t2", "fail", NOW)], NOW)
    db = str(tmp_path / "e.duckdb")
    assert state.load(s3, "raw", db, results, NOW) == 2
    con = duckdb.connect(db, read_only=True)
    assert con.execute("select count(*) from main.elementary_test_results").fetchone() == (2,)
    con.close()
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="raw")["Contents"]]
    assert keys == ["state/elementary/test_results/dt=2026-11-23/rows.parquet"]
