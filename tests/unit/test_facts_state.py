"""Closed-day facts (ADR-024): the plan, one write per closed day, and self-healing after misses."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pytest
from moto import mock_aws

from headway_transform import facts_state, handler

TZ = "America/New_York"
# 06:00 local on the next day is 11:00 UTC in November: the 21st closes at 2026-11-22T11:00Z.
BEFORE_CLOSE = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
AFTER_CLOSE = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


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


def _days(first: str, last: str) -> list[date]:
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    return [date.fromordinal(n) for n in range(a.toordinal(), b.toordinal() + 1)]


def _build(path: Path, days: list[date]) -> str:
    """The database a run leaves: every fact has a row for each service day of the window."""
    con = duckdb.connect(str(path))
    for table in facts_state.FACT_VERSIONS:
        con.execute(f"create table {table} (service_date date, n bigint)")
        for d in days:
            con.execute(f"insert into {table} values ('{d}', 1)")
    con.close()
    return str(path)


def _run(s3: Any, tmp_path: Path, now: datetime, data: list[date]) -> tuple[Any, Any]:
    """One transform run's facts handling: plan, (fake) build of the window, persist."""
    plan = facts_state.plan(s3, "raw", now, 7)
    db = _build(tmp_path / f"{now:%Y%m%dT%H}.duckdb", data)
    written = facts_state.write_closed_days(
        s3, "raw", db, now, timezone=TZ, lookback_days=7, skip=plan.persisted
    )
    return plan, written


def _persisted(s3: Any, table: str = "fct_headways") -> list[str]:
    prefix = f"state/facts/{table}/metric_version=1/"
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="raw", Prefix=prefix).get("Contents", [])]
    return sorted(k.split("service_date=")[1].split("/")[0] for k in keys)


def _etag(s3: Any, day: str) -> str:
    key = facts_state.key_for("fct_headways", 1, date.fromisoformat(day))
    return str(s3.head_object(Bucket="raw", Key=key)["ETag"])


DATA = _days("2026-11-16", "2026-11-23")


def test_the_first_run_computes_every_day_and_persists_the_closed_ones(
    s3: Any, tmp_path: Path
) -> None:
    plan, written = _run(s3, tmp_path, BEFORE_CLOSE, DATA)
    assert plan.persisted == [] and plan.compute == facts_state.window_days(BEFORE_CLOSE, 7)
    # the 21st has not closed yet; the 15th has no data in this fixture and is persisted empty
    assert _persisted(s3) == [
        "2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20",
    ]  # fmt: skip
    assert written.written["fct_headways"] == _persisted(s3)


def test_a_day_that_closes_between_two_runs_is_computed_and_persisted_exactly_once(
    s3: Any, tmp_path: Path
) -> None:
    _run(s3, tmp_path, BEFORE_CLOSE, DATA)
    before = _persisted(s3)
    assert "2026-11-21" not in before

    plan, written = _run(s3, tmp_path, AFTER_CLOSE, DATA)
    assert date(2026, 11, 21) in plan.compute  # it was open for the previous run
    assert written.written == {t: ["2026-11-21"] for t in facts_state.FACT_VERSIONS}
    assert _persisted(s3) == [*before, "2026-11-21"]
    etag = _etag(s3, "2026-11-21")

    plan, written = _run(s3, tmp_path, AFTER_CLOSE.replace(hour=13), DATA)
    assert date(2026, 11, 21) in plan.persisted and date(2026, 11, 21) not in plan.compute
    assert written.n_written == 0  # nothing new: the 22nd is still open
    assert _etag(s3, "2026-11-21") == etag  # never rewritten


def test_a_run_after_missed_runs_persists_every_unpersisted_closed_day(
    s3: Any, tmp_path: Path
) -> None:
    _run(s3, tmp_path, BEFORE_CLOSE, DATA)
    # no run for three days: the 21st, 22nd and 23rd all closed in the meantime
    later = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    data = _days("2026-11-17", "2026-11-24")
    plan, written = _run(s3, tmp_path, later, data)
    assert {date(2026, 11, d) for d in (21, 22, 23)} <= set(plan.compute)  # none was persisted
    assert written.written == {
        t: ["2026-11-21", "2026-11-22", "2026-11-23"] for t in facts_state.FACT_VERSIONS
    }
    assert _persisted(s3)[-3:] == ["2026-11-21", "2026-11-22", "2026-11-23"]
    # and the run after that loads them all
    plan, written = _run(s3, tmp_path, later.replace(hour=14), data)
    assert [d.isoformat() for d in plan.persisted][-3:] == [
        "2026-11-21",
        "2026-11-22",
        "2026-11-23",
    ]
    assert written.n_written == 0


def test_a_day_missing_one_table_is_computed_again(s3: Any, tmp_path: Path) -> None:
    _run(s3, tmp_path, AFTER_CLOSE, DATA)
    key = facts_state.key_for("fct_stop_predictions", 1, date(2026, 11, 20))
    s3.delete_object(Bucket="raw", Key=key)  # e.g. an interrupted write
    plan, written = _run(s3, tmp_path, AFTER_CLOSE.replace(hour=13), DATA)
    assert date(2026, 11, 20) in plan.compute
    assert written.written == {"fct_stop_predictions": ["2026-11-20"]}  # only the gap is filled


def test_a_new_version_starts_from_nothing_next_to_the_old_one(s3: Any, tmp_path: Path) -> None:
    _run(s3, tmp_path, AFTER_CLOSE, DATA)
    bumped = {**facts_state.FACT_VERSIONS, "fct_headways": 2}
    plan = facts_state.plan(s3, "raw", AFTER_CLOSE, 7, bumped)
    assert plan.persisted == [] and len(plan.compute) == 9
    assert _persisted(s3)  # version 1 stays where it was


def test_the_plan_is_reported_as_computed_versus_loaded_days(s3: Any, tmp_path: Path) -> None:
    _run(s3, tmp_path, BEFORE_CLOSE, DATA)
    plan, _ = _run(s3, tmp_path, AFTER_CLOSE, DATA)
    summary = plan.summary()
    assert summary["facts_loaded_days"][0] == "2026-11-15"
    assert (
        "2026-11-21" in summary["facts_computed_days"]
        and "2026-11-22" in summary["facts_computed_days"]
    )
    assert set(summary["facts_loaded_days"]).isdisjoint(summary["facts_computed_days"])
    assert json.dumps(summary)  # it goes into the run's summary log line


def test_the_dbt_vars_split_the_window_into_compute_and_persisted_days(
    s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {
        "RAW_BUCKET": "raw", "PUBLIC_BUCKET": "pub", "CITY_ID": "boston",
        "HEADWAY_ENABLE_R2": "true",
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    _run(s3, tmp_path, AFTER_CLOSE, DATA)
    plan = handler._facts_plan(s3, 7, AFTER_CLOSE.replace(hour=14))
    assert plan is not None
    variables = json.loads(handler._variables(7, AFTER_CLOSE, plan))
    assert variables["facts_root"] == "s3://raw/state/facts"
    assert variables["fact_versions"] == facts_state.FACT_VERSIONS
    assert set(variables["compute_days"]).isdisjoint(variables["persisted_days"])
    assert len(variables["compute_days"]) + len(variables["persisted_days"]) == 9
    assert "2026-11-21" in variables["persisted_days"]


def test_the_kill_switch_and_a_failed_plan_compute_everything(
    s3: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    for k, v in {"RAW_BUCKET": "raw", "PUBLIC_BUCKET": "pub", "CITY_ID": "boston"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    monkeypatch.setenv("HEADWAY_INCREMENTAL_FACTS", "false")
    assert handler._facts_plan(s3, 7, AFTER_CLOSE) is None
    monkeypatch.setenv("HEADWAY_INCREMENTAL_FACTS", "true")
    monkeypatch.setenv("RAW_BUCKET", "no-such-bucket")  # listing fails: cost time, not correctness
    assert handler._facts_plan(s3, 7, AFTER_CLOSE) is None
    variables = json.loads(handler._variables(7, AFTER_CLOSE, None))
    assert "compute_days" not in variables


def test_a_rebuild_payload_is_validated() -> None:
    assert facts_state.validate_rebuild(
        {"from": "2026-11-10", "to": "2026-11-12", "metric_version": 1}
    ) == (date(2026, 11, 10), date(2026, 11, 12), 1)
    for bad in (
        {"from": "2026-11-10", "to": "2026-11-12"},
        {"from": "2026-11-12", "to": "2026-11-10", "metric_version": 1},
        {"from": "2026-11-01", "to": "2026-11-20", "metric_version": 1},
        {"from": "2026-11-10", "to": "2026-11-12", "metric_version": 9},
    ):
        with pytest.raises(ValueError):
            facts_state.validate_rebuild(bad)


def test_a_closed_day_without_rows_is_persisted_empty_and_not_computed_again(
    s3: Any, tmp_path: Path
) -> None:
    data = [d for d in DATA if d != date(2026, 11, 18)]  # an outage day: no rows in any fact
    _run(s3, tmp_path, AFTER_CLOSE, data)
    assert "2026-11-18" in _persisted(s3)
    key = facts_state.key_for("fct_headways", 1, date(2026, 11, 18))
    path = tmp_path / "part.parquet"
    s3.download_file("raw", key, str(path))
    assert duckdb.sql(f"select count(*) from read_parquet('{path}')").fetchone() == (0,)
    assert [
        c[0] for c in duckdb.sql(f"describe select * from read_parquet('{path}')").fetchall()
    ] == [
        "service_date",
        "n",
    ]  # the schema is kept
    plan, written = _run(s3, tmp_path, AFTER_CLOSE.replace(hour=13), data)
    assert date(2026, 11, 18) in plan.persisted
    assert written.n_written == 0
