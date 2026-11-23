import io
import json
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from headway_transform import cost, handler, publish

NOW = datetime(2026, 11, 22, 7, 20, tzinfo=UTC)
GOLD = "public/boston/gold"


class StubCostExplorer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_cost_and_usage(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)

        def period(day: str, **services: str) -> dict[str, Any]:
            return {
                "TimePeriod": {"Start": day, "End": day},
                "Groups": [
                    {"Keys": [name], "Metrics": {"UnblendedCost": {"Amount": amount}}}
                    for name, amount in services.items()
                ],
            }

        if "NextPageToken" not in kwargs:
            return {
                "NextPageToken": "page2",
                "ResultsByTime": [period("2026-11-20", Lambda="0.25", S3="0.05")],
            }
        return {"ResultsByTime": [period("2026-11-21", Lambda="0.40")]}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "RAW_BUCKET": "raw-bucket",
        "PUBLIC_BUCKET": "public-bucket",
        "CITY_ID": "boston",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="public-bucket")
        yield s3


@pytest.fixture
def pipeline_db(tmp_path: Path) -> str:
    path = str(tmp_path / "headway.duckdb")
    con = duckdb.connect(path)
    con.execute("create table agg_pipeline_health (scheduled_at timestamptz, n_captures bigint)")
    con.execute(
        "insert into agg_pipeline_health values "
        "('2026-11-20 10:00:00+00', 3000), ('2026-11-20 11:00:00+00', 1000), "
        "('2026-11-21 10:00:00+00', 4000)"
    )
    con.close()
    return path


# ---------------------------------------------------------------- cost writer


def test_due_gate_is_once_per_day_after_six_utc() -> None:
    yesterday = datetime(2026, 11, 21, 6, 20, tzinfo=UTC)
    today = datetime(2026, 11, 22, 6, 20, tzinfo=UTC)
    assert not cost.due(None, datetime(2026, 11, 22, 5, 59, tzinfo=UTC))
    assert cost.due(None, NOW)
    assert cost.due(yesterday, NOW)
    assert not cost.due(today, NOW)


def test_write_cost_daily_builds_rows_with_capture_counts(aws: Any, pipeline_db: str) -> None:
    ce = StubCostExplorer()
    n = cost.write_cost_daily(aws, "public-bucket", GOLD, ce, pipeline_db, NOW)
    assert n == 3

    call = ce.calls[0]
    assert call["Granularity"] == "DAILY"
    assert call["GroupBy"] == [{"Type": "DIMENSION", "Key": "SERVICE"}]
    assert call["Filter"] == {"Tags": {"Key": "project", "Values": ["headway"]}}
    assert call["TimePeriod"]["End"] == "2026-11-22"

    body = aws.get_object(Bucket="public-bucket", Key=f"{GOLD}/cost_daily.parquet")["Body"].read()
    rows = pq.read_table(io.BytesIO(body)).to_pylist()
    first = rows[0]
    assert first["date"] == date(2026, 11, 20)
    assert first["service"] == "Lambda"
    assert first["cost_usd"] == pytest.approx(0.25)
    assert first["day_total_usd"] == pytest.approx(0.30)
    assert first["captures"] == 4000
    assert first["cost_per_1000_captures"] == pytest.approx(0.30 / 4000 * 1000)
    assert [r["service"] for r in rows] == ["Lambda", "S3", "Lambda"]


def test_cost_per_1000_is_null_without_captures() -> None:
    rows = cost.build_rows({date(2026, 11, 19): {"S3": 0.1}}, {})
    assert rows == [(date(2026, 11, 19), "S3", 0.1, 0.1, None, None)]


def test_write_cost_daily_skips_when_already_written_today(aws: Any, pipeline_db: str) -> None:
    ce = StubCostExplorer()
    assert cost.write_cost_daily(aws, "public-bucket", GOLD, ce, pipeline_db, NOW) == 3
    ce.calls.clear()
    assert cost.write_cost_daily(aws, "public-bucket", GOLD, ce, pipeline_db, NOW) is None
    assert ce.calls == []


# ---------------------------------------------------------------- publish


class FakeConnection:
    def __init__(self) -> None:
        self.sql: list[str] = []
        self.closed = False

    def execute(self, sql: str) -> None:
        self.sql.append(sql)

    def close(self) -> None:
        self.closed = True


def test_publish_replaces_one_table_per_gold_file() -> None:
    con = FakeConnection()
    out = publish.publish_motherduck(
        "tok", "s3://b/public/boston/gold", ["dim_route", "cost_daily"], connect=lambda t: con
    )
    assert out == ["dim_route", "cost_daily"]
    assert con.closed
    replaced = [s for s in con.sql if s.startswith("create or replace table")]
    assert replaced == [
        "create or replace table main.dim_route as "
        "select * from read_parquet('s3://b/public/boston/gold/dim_route.parquet')",
        "create or replace table main.cost_daily as "
        "select * from read_parquet('s3://b/public/boston/gold/cost_daily.parquet')",
    ]


# ---------------------------------------------------------------- handler


@pytest.fixture
def fakes(aws: Any, monkeypatch: pytest.MonkeyPatch, pipeline_db: str) -> dict[str, Any]:
    state: dict[str, Any] = {"dbt": [], "published": [], "outcome": handler.DbtOutcome(True, 9, 0)}
    monkeypatch.setenv("HEADWAY_DUCKDB_PATH", pipeline_db)
    monkeypatch.setattr(handler, "_now", lambda: NOW)

    def fake_dbt(args: list[str]) -> handler.DbtOutcome:
        state["dbt"].append(args)
        if args[0] == "run-operation":
            manifest = {"row_counts": {"dim_route": 5, "dim_stop": 7}}
            aws.put_object(
                Bucket="public-bucket", Key=f"{GOLD}/manifest.json", Body=json.dumps(manifest)
            )
            if not state.get("skip_parquet"):
                for table in publish.GOLD_TABLES:
                    aws.put_object(Bucket="public-bucket", Key=f"{GOLD}/{table}.parquet", Body=b"x")
            return handler.DbtOutcome(True)
        return state["outcome"]  # type: ignore[no-any-return]

    def fake_publish(token: str, gold_url: str, tables: list[str]) -> list[str]:
        state["published"].append((token, gold_url, list(tables)))
        return list(tables)

    ssm = boto3.client("ssm")
    ssm.put_parameter(Name=handler.TOKEN_PARAMETER, Value="md-secret", Type="SecureString")
    monkeypatch.setattr(handler, "run_dbt", fake_dbt)
    monkeypatch.setattr(handler.publish, "publish_motherduck", fake_publish)
    monkeypatch.setattr(
        handler.cost, "fetch_costs", lambda ce, today: {date(2026, 11, 20): {"S3": 1.0}}
    )
    return state


def _summary(caplog: pytest.LogCaptureFixture) -> dict[str, Any]:
    lines = [r.message for r in caplog.records if r.message.startswith("{")]
    assert len(lines) == 1
    return json.loads(lines[0])  # type: ignore[no-any-return]


def test_handler_builds_exports_costs_and_publishes(
    fakes: dict[str, Any], aws: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    summary = handler.lambda_handler({}, None)

    build, test, export = fakes["dbt"]
    assert build[:3] == ["build", "--exclude", "resource_type:test"]
    assert test[0] == "test"
    assert json.loads(build[build.index("--vars") + 1]) == {
        "curated_root": "s3://raw-bucket",
        "city_prefix": "boston",
        "lookback_days": 7,
    }
    assert export[0] == "run-operation" and export[1] == "export_gold"
    assert json.loads(export[export.index("--args") + 1]) == {
        "target_dir": "s3://public-bucket/public/boston/gold"
    }

    token, gold_url, tables = fakes["published"][0]
    assert token == "md-secret"
    assert gold_url == "s3://public-bucket/public/boston/gold"
    assert tables == [*publish.GOLD_TABLES, "cost_daily"]
    assert aws.head_object(Bucket="public-bucket", Key=f"{GOLD}/cost_daily.parquet")

    logged = _summary(caplog)
    assert logged == summary
    assert summary["dbt_status"] == "success"
    assert summary["tests_failed"] == 0 and summary["tests_passed"] == 9
    assert summary["rows_exported"] == 12
    assert summary["cost_rows"] == 1
    assert set(summary["step_ms"]) == {"dbt_build", "export_gold", "cost", "publish"}


def test_test_failures_do_not_block_export(
    fakes: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    fakes["outcome"] = handler.DbtOutcome(False, tests_passed=8, tests_failed=2)
    summary = handler.lambda_handler({}, None)
    assert [a[0] for a in fakes["dbt"]] == ["build", "test", "run-operation"]
    assert summary["dbt_status"] == "tests_failed"
    assert summary["tests_failed"] == 2
    assert fakes["published"]


def test_missing_export_objects_fail_loudly(fakes: dict[str, Any]) -> None:
    fakes["skip_parquet"] = True
    with pytest.raises(handler.ExportError, match=r"gold/agg_line_reliability_hourly\.parquet"):
        handler.lambda_handler({}, None)
    assert not fakes["published"]


def test_model_errors_stop_before_export(fakes: dict[str, Any]) -> None:
    fakes["outcome"] = handler.DbtOutcome(False, node_errors=["agg_pipeline_health"])
    with pytest.raises(handler.TransformError, match="agg_pipeline_health"):
        handler.lambda_handler({}, None)
    assert [a[0] for a in fakes["dbt"]] == ["build"]
    assert not fakes["published"]


def test_cost_failure_is_logged_not_raised(
    fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    def boom(ce: Any, today: date) -> Any:
        raise RuntimeError("ce unavailable")

    monkeypatch.setattr(handler.cost, "fetch_costs", boom)
    summary = handler.lambda_handler({}, None)
    assert summary["cost_rows"] is None
    assert "ce unavailable" in summary["cost_error"]
    assert fakes["published"][0][2] == list(publish.GOLD_TABLES)


def test_publish_failure_is_logged_then_raised(
    fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    def boom(token: str, url: str, tables: list[str]) -> list[str]:
        raise RuntimeError("md down")

    monkeypatch.setattr(handler.publish, "publish_motherduck", boom)
    with pytest.raises(RuntimeError, match="md down"):
        handler.lambda_handler({}, None)
    logged = _summary(caplog)
    assert "md down" in logged["publish_error"]
    assert logged["gold_exported"] is True


def test_prepare_extension_dir_copies_baked_extensions_once(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    baked = tmp_path / "baked"
    (baked / "v1" / "arm").mkdir(parents=True)
    (baked / "v1" / "arm" / "x.duckdb_extension").write_text("x")
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("HEADWAY_DUCKDB_EXTENSIONS_SRC", str(baked))
    monkeypatch.setenv("HEADWAY_DUCKDB_EXTENSIONS_RUNTIME", str(runtime))
    assert handler.prepare_extension_dir() == str(runtime)
    assert (runtime / "v1" / "arm" / "x.duckdb_extension").read_text() == "x"
    assert os.environ["HEADWAY_DUCKDB_EXTENSIONS"] == str(runtime)
    (runtime / "marker").write_text("kept")
    handler.prepare_extension_dir()  # second cold-start call is a no-op
    assert (runtime / "marker").read_text() == "kept"
    assert handler.assert_extension_dir_writable(str(runtime)) is True


# ---------------------------------------------------------------- dbt runner


def _node_result(resource_type: str, status: str, name: str = "n") -> Any:
    from types import SimpleNamespace as NS

    return NS(status=status, node=NS(resource_type=resource_type, name=name))


def _fake_dbt(monkeypatch: pytest.MonkeyPatch, result: Any) -> list[list[str]]:
    import sys
    import types

    calls: list[list[str]] = []

    class Runner:
        def invoke(self, args: list[str]) -> Any:
            calls.append(args)
            return result

    for name in ("dbt", "dbt.cli", "dbt.cli.main"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    sys.modules["dbt.cli.main"].dbtRunner = Runner  # type: ignore[attr-defined]
    return calls


def test_run_dbt_counts_tests_and_node_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace as NS

    results = [
        _node_result("test", "pass"),
        _node_result("test", "warn"),
        _node_result("test", "fail"),
        _node_result("model", "success"),
        _node_result("model", "error", "dim_stop"),
    ]
    calls = _fake_dbt(monkeypatch, NS(exception=None, success=False, result=NS(results=results)))
    out = handler.run_dbt(["build"])
    assert (out.tests_passed, out.tests_failed, out.node_errors) == (2, 1, ["dim_stop"])
    assert not out.success
    assert calls[0][0] == "build" and "lambda" in calls[0] and "--target-path" in calls[0]


def test_run_dbt_raises_on_invocation_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace as NS

    _fake_dbt(monkeypatch, NS(exception=ValueError("bad profile"), success=False, result=None))
    with pytest.raises(handler.TransformError, match="bad profile"):
        handler.run_dbt(["build"])


def test_run_operation_failure_raises_with_log_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace as NS

    (tmp_path / "dbt.log").write_text("noise\nCatalog Error: Table agg_x does not exist\n")
    monkeypatch.setattr(handler, "DBT_LOG_PATH", str(tmp_path))
    _fake_dbt(monkeypatch, NS(exception=None, success=False, result=NS(results=[])))
    with pytest.raises(handler.TransformError, match="Catalog Error: Table agg_x"):
        handler.run_dbt(["run-operation", "export_gold"])
