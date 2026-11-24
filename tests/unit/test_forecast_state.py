"""Closed-day forecast tables: written once under state/, only when closed, backfill by version."""

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pytest
from moto import mock_aws

from headway_transform import forecast_state


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


def _db(path: Path) -> str:
    con = duckdb.connect(str(path))
    con.execute("create table fct_forecast_examples (service_date date, line varchar, x double)")
    con.execute("create table int_forecast_gate_days (service_date date, line varchar, b boolean)")
    for d in ("2026-11-20", "2026-11-21", "2026-11-22"):
        con.execute(f"insert into fct_forecast_examples values ('{d}', 'Red', 1.0)")
        con.execute(f"insert into int_forecast_gate_days values ('{d}', 'Red', true)")
    con.close()
    return str(path)


def _keys(s3: Any) -> list[str]:
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket="raw").get("Contents", []))


def test_only_closed_days_are_written_and_never_again(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "t.duckdb")
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)  # the 22nd has not closed (06:00 local on 23rd)
    kw: dict[str, Any] = {"timezone": "America/New_York", "lookback_days": 7}
    first = forecast_state.write_closed_days(s3, "raw", db, now, **kw)
    assert first.n_written == 4  # two tables x the 20th and 21st
    assert _keys(s3) == sorted(
        f"state/{n}/metric_version=1/service_date={d}/part.parquet"
        for n in ("forecast_examples", "forecast_gate_days")
        for d in ("2026-11-20", "2026-11-21")
    )
    second = forecast_state.write_closed_days(s3, "raw", db, now, **kw)
    assert second.n_written == 0 and second.existing == 4


def test_backfill_writes_only_the_requested_days_at_the_version(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "t.duckdb")
    now = datetime(2026, 12, 1, tzinfo=UTC)
    res = forecast_state.write_closed_days(
        s3, "raw", db, now, timezone="America/New_York", lookback_days=None,
        days=[date(2026, 11, 21)], only_version=1,
    )  # fmt: skip
    assert res.n_written == 2
    assert all("service_date=2026-11-21" in k for k in _keys(s3))


def test_rebuild_payload_validation() -> None:
    ok = {"from": "2026-11-01", "to": "2026-11-03", "metric_version": 1}
    assert forecast_state.validate_rebuild(ok)[2] == 1
    for bad in (
        {"from": "2026-11-03", "to": "2026-11-01", "metric_version": 1},
        {"from": "2026-11-01", "to": "2026-12-31", "metric_version": 1},
        {"from": "2026-11-01", "to": "2026-11-03", "metric_version": 9},
        {"to": "2026-11-03"},
    ):
        with pytest.raises(ValueError):
            forecast_state.validate_rebuild(bad)
