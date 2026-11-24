"""Forecast serving glue in the transform: reports, scoring, labels, never a fabricated forecast."""

from pathlib import Path
from typing import Any

import boto3
import duckdb
import numpy as np
import pandas as pd
import pytest
from moto import mock_aws

from headway_transform import forecast_serving, handler, publish


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in {"AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="raw")
        client.create_bucket(Bucket="public")
        yield client


def _db(path: Path, gate_passed: tuple[str, ...]) -> str:
    con = duckdb.connect(str(path))
    con.execute(
        "create table agg_forecast_next_hour (line varchar, direction_id integer, stop_id varchar, "
        "prediction_ts timestamptz, horizon_min integer, n_trips bigint, model_forecast_s double, "
        "n_model integer, persistence double, forecast_status varchar, status_reason varchar, "
        "model_version integer)"
    )
    con.execute(
        "create table int_forecast_serving_features (line varchar, direction_id integer, "
        "stop_id varchar, prediction_ts timestamptz, horizon_min integer, x double)"
    )
    con.execute("create table agg_forecast_gate_status (line varchar, gate_passed boolean)")
    for line in ("Red", "Blue", "Orange"):
        for stop in ("a", "b"):
            con.execute(
                f"insert into agg_forecast_next_hour values ('{line}', 0, '{stop}', "
                "'2026-11-22 15:45:00+00', 15, 2, null, 0, 10, 'baselines_only', 'x', null)"
            )
            for _ in range(2):  # two trip stops per slot
                con.execute(
                    f"insert into int_forecast_serving_features values ('{line}', 0, '{stop}', "
                    "'2026-11-22 15:45:00+00', 15, 100)"
                )
        con.execute(
            f"insert into agg_forecast_gate_status values ('{line}', {line in gate_passed})"
        )
    con.close()
    return str(path)


class FakeModel:
    version = 3
    from_cache = False
    lines = ["Red", "Blue"]

    def score(self, rows: pd.DataFrame) -> np.ndarray:
        assert set(rows["line"]) <= set(self.lines)
        return np.full(len(rows), 42.0)


def _rows(db: str) -> dict[str, tuple[Any, ...]]:
    con = duckdb.connect(db, read_only=True)
    got = con.execute(
        "select line, any_value(model_forecast_s), any_value(forecast_status), "
        "any_value(status_reason), any_value(n_model) from agg_forecast_next_hour group by line"
    ).fetchall()
    con.close()
    return {r[0]: r for r in got}


def test_no_latest_json_serves_baselines_only_with_a_reason(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "t.duckdb", gate_passed=("Red",))
    out = forecast_serving.serve(s3, "raw", db, load_model=lambda *_: None)
    assert out["forecast_serving_status"] == "baselines_only" and out["forecast_model_rows"] == 0
    rows = _rows(db)
    assert all(r[1] is None and r[2] == "baselines_only" for r in rows.values())
    assert rows["Red"][3] == "no approved model yet (no latest.json)"
    assert rows["Blue"][3] == "training gate not passed"


def test_only_lines_in_the_model_and_past_the_gate_get_a_forecast(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "t.duckdb", gate_passed=("Red", "Orange"))  # Blue in model, gate not passed
    out = forecast_serving.serve(s3, "raw", db, load_model=lambda *_: FakeModel())
    rows = _rows(db)
    assert rows["Red"][1:3] == (42.0, "model") and rows["Red"][3] == "model v3"
    assert rows["Red"][4] == 2  # trip stops averaged into the slot
    assert rows["Blue"][1] is None and rows["Blue"][3] == "training gate not passed"
    assert rows["Orange"][1] is None and rows["Orange"][3] == "line is not in the approved model"
    assert out["forecast_serving_status"] == "model" and out["forecast_model_rows"] == 2
    assert out["forecast_baseline_rows"] == 6 and out["forecast_model_version"] == 3


def test_an_unreadable_model_never_fails_the_run_and_is_labelled(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "t.duckdb", gate_passed=("Red",))

    def boom(*_: Any) -> Any:
        raise OSError("no libgomp")

    out = forecast_serving.serve(s3, "raw", db, load_model=boom)
    assert out["forecast_serving_status"] == "baselines_only"
    assert out["forecast_model_error"] == "OSError"
    assert _rows(db)["Red"][3] == "model unavailable (OSError)"


def test_a_model_that_cannot_score_a_line_is_not_a_forecast(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "t.duckdb", gate_passed=("Red",))

    class Bad(FakeModel):
        def score(self, rows: pd.DataFrame) -> np.ndarray:
            raise ValueError("bad features")

    with pytest.raises(ValueError):  # the handler wrapper turns this into baselines_only
        forecast_serving.serve(s3, "raw", db, load_model=lambda *_: Bad())
    assert all(r[1] is None for r in _rows(db).values())


def test_handler_wrapper_reports_a_failed_serving_step(
    s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RAW_BUCKET", "raw")
    monkeypatch.setattr(
        forecast_serving, "serve", lambda *a, **k: (_ for _ in ()).throw(ValueError("x"))
    )
    out = handler._forecast_serving(s3, str(tmp_path / "t.duckdb"))  # noqa: SLF001
    assert out["forecast_serving_status"] == "failed"


def test_reports_are_loaded_or_dropped(
    s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = str(tmp_path / "t.duckdb")
    con = duckdb.connect(db)
    con.execute("create table raw_forecast_gate_status as select 'stale' as line")
    con.close()
    src = tmp_path / "gate.parquet"
    duckdb.sql("select 'Red' as line, 25 as n_days, true as gate_passed").write_parquet(str(src))
    s3.upload_file(str(src), "raw", forecast_serving.REPORTS["raw_forecast_gate_status"])
    out = forecast_serving.load_reports(s3, "raw", db)
    assert out == {"raw_forecast_backtest": 0, "raw_forecast_gate_status": 1}
    con = duckdb.connect(db, read_only=True)
    assert con.execute("select line from raw_forecast_gate_status").fetchall() == [("Red",)]
    tables = {
        r[0] for r in con.execute("select table_name from information_schema.tables").fetchall()
    }
    assert (
        "raw_forecast_backtest" not in tables
    )  # absent report: the dbt model reads an empty table
    con.close()


def test_as_of_follows_the_clock() -> None:
    from datetime import UTC, datetime

    out = handler._with_forecast_as_of('{"a": 1}', datetime(2026, 11, 22, 15, 47, 9, tzinfo=UTC))  # noqa: SLF001
    assert '"forecast_as_of": "2026-11-22T15:47:09Z"' in out


def test_stale_backtest_object_is_removed_when_this_export_did_not_write_it(s3: Any) -> None:
    key = "public/boston/gold/agg_forecast_backtest.parquet"
    s3.put_object(Bucket="public", Key=key, Body=b"old")
    gone = handler.drop_stale_r3_tables(s3, "public", "public/boston/gold", {"dim_stop": 3})
    assert gone == [key]
    assert "Contents" not in s3.list_objects_v2(Bucket="public", Prefix=key)
    s3.put_object(Bucket="public", Key=key, Body=b"new")
    kept = handler.drop_stale_r3_tables(
        s3, "public", "public/boston/gold", {"agg_forecast_backtest": 5}
    )
    assert kept == [] and publish.R3_OPTIONAL_TABLES == ("agg_forecast_backtest",)
