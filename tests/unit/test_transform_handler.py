import io
import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from headway_transform import cost, handler, history, publish

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


def _fake_dbt_duckdb(monkeypatch: pytest.MonkeyPatch, env: Any) -> Any:
    """Stand-in for dbt-duckdb's process-wide connection manager (dbt is not in this env)."""
    import sys
    import threading
    import types

    class Manager:
        _LOCK = threading.RLock()
        _ENV = env

        @classmethod
        def close_all_connections(cls) -> None:
            cls._ENV = None

    module = types.ModuleType("dbt.adapters.duckdb.connections")
    module.DuckDBConnectionManager = Manager  # type: ignore[attr-defined]
    for name in ("dbt", "dbt.adapters", "dbt.adapters.duckdb"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "dbt.adapters.duckdb.connections", module)
    return Manager


def test_cost_reads_the_database_dbt_still_holds_open_after_release(
    pipeline_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Env:  # what dbt-duckdb keeps: one read-write connection with its own configuration
        conn = duckdb.connect(pipeline_db, config={"threads": "2"})

        def close(self) -> None:
            self.conn.close()

    env = Env()
    manager = _fake_dbt_duckdb(monkeypatch, env)
    with pytest.raises(duckdb.ConnectionException, match="different configuration"):
        cost.captures_by_day(pipeline_db)

    handler.release_dbt_connections()

    assert manager._ENV is None
    assert cost.captures_by_day(pipeline_db) == {date(2026, 11, 20): 4000, date(2026, 11, 21): 4000}


def test_release_dbt_connections_is_a_no_op_without_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_dbt_duckdb(monkeypatch, None)
    handler.release_dbt_connections()


def test_gold_objects_are_cacheable_for_five_minutes(fakes: dict[str, Any], aws: Any) -> None:
    handler.lambda_handler({}, None)
    keys = [f"{GOLD}/{t}.parquet" for t in publish.gold_tables()]
    keys += [f"{GOLD}/manifest.json", f"{GOLD}/cost_daily.parquet"]
    for key in keys:
        head = aws.head_object(Bucket="public-bucket", Key=key)
        assert head["CacheControl"] == "max-age=300", key
    assert (
        aws.head_object(Bucket="public-bucket", Key=f"{GOLD}/manifest.json")["ContentType"]
        == "application/json"
    )


def test_cost_report_is_written_after_dbt_connections_are_released(
    fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    monkeypatch.setattr(handler, "release_dbt_connections", lambda: order.append("release"))
    real = handler.cost.write_cost_daily

    def spy(*args: Any, **kwargs: Any) -> Any:
        order.append("cost")
        return real(*args, **kwargs)

    monkeypatch.setattr(handler.cost, "write_cost_daily", spy)
    handler.lambda_handler({}, None)
    assert order[order.index("cost") - 1] == "release"  # cost reads the file dbt had open


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
                for table in publish.GOLD_TABLES + publish.R2_GOLD_TABLES:
                    aws.put_object(Bucket="public-bucket", Key=f"{GOLD}/{table}.parquet", Body=b"x")
            return handler.DbtOutcome(True)
        return state.get("by_command", {}).get(args[0], state["outcome"])  # type: ignore[no-any-return]

    state["history_publish"] = None

    def fake_publish(
        token: str, gold_url: str, tables: list[str], **history_args: Any
    ) -> list[str]:
        state["published"].append((token, gold_url, list(tables)))
        state["history_publish"] = history_args
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

    load, build, test, export = fakes["dbt"]
    assert load[:3] == ["run", "--select", "tag:s3_load"]
    assert build[:4] == ["build", "--exclude", "resource_type:test", "tag:s3_load"]
    assert "tag:r2" in build  # Release 2 models are off by default
    assert test[0] == "test" and test[1:3] == ["--exclude", "tag:r2"]
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
    assert set(summary["step_ms"]) == {
        "s3_load", "dbt_build", "dbt_test", "export_gold", "cost", "observability", "publish",
    }  # fmt: skip
    assert all(isinstance(ms, int) and ms >= 0 for ms in summary["step_ms"].values())


def test_test_failures_do_not_block_export(
    fakes: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    fakes["outcome"] = handler.DbtOutcome(False, tests_passed=8, tests_failed=2)
    summary = handler.lambda_handler({}, None)
    assert [a[0] for a in fakes["dbt"]] == ["run", "build", "test", "run-operation"]
    assert summary["dbt_status"] == "tests_failed"
    assert summary["tests_failed"] == 2
    assert fakes["published"]


def test_missing_export_objects_fail_loudly(fakes: dict[str, Any]) -> None:
    fakes["skip_parquet"] = True
    with pytest.raises(handler.ExportError, match=r"gold/agg_line_reliability_hourly\.parquet"):
        handler.lambda_handler({}, None)
    assert not fakes["published"]


def test_model_errors_stop_before_export(fakes: dict[str, Any]) -> None:
    fakes["by_command"] = {"build": handler.DbtOutcome(False, node_errors=["agg_pipeline_health"])}
    with pytest.raises(handler.TransformError, match="agg_pipeline_health"):
        handler.lambda_handler({}, None)
    assert [a[0] for a in fakes["dbt"]] == ["run", "build"]
    assert not fakes["published"]


def test_s3_load_errors_stop_before_the_build(fakes: dict[str, Any]) -> None:
    fakes["by_command"] = {"run": handler.DbtOutcome(False, node_errors=["stg_manifests"])}
    with pytest.raises(handler.TransformError, match="S3 load failed: stg_manifests"):
        handler.lambda_handler({}, None)
    assert [a[0] for a in fakes["dbt"]] == ["run"]


# ---------------------------------------------------------------- run lock

LOCK_KEY = "state/boston/transform.lock"


def _lock_body(started: datetime, run_id: str = "other") -> bytes:
    return json.dumps({"run_id": run_id, "started_at": started.isoformat()}).encode()


def test_run_lock_is_released_after_a_run(fakes: dict[str, Any], aws: Any) -> None:
    handler.lambda_handler({}, None)
    assert "Contents" not in aws.list_objects_v2(Bucket="raw-bucket", Prefix="state/boston/")


def test_run_lock_is_released_when_the_run_fails(fakes: dict[str, Any], aws: Any) -> None:
    fakes["by_command"] = {"build": handler.DbtOutcome(False, node_errors=["agg_pipeline_health"])}
    with pytest.raises(handler.TransformError):
        handler.lambda_handler({}, None)
    assert "Contents" not in aws.list_objects_v2(Bucket="raw-bucket", Prefix="state/boston/")


def test_a_concurrent_run_exits_cleanly_without_touching_the_lock(
    fakes: dict[str, Any], aws: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    held = _lock_body(NOW - timedelta(minutes=2))
    aws.put_object(Bucket="raw-bucket", Key=LOCK_KEY, Body=held)
    summary = handler.lambda_handler({}, None)
    assert summary == {"skipped": "locked"}
    assert _summary(caplog) == {"skipped": "locked"}
    assert fakes["dbt"] == [] and not fakes["published"]
    assert aws.get_object(Bucket="raw-bucket", Key=LOCK_KEY)["Body"].read() == held


def test_a_stale_lock_is_replaced_and_the_run_proceeds(fakes: dict[str, Any], aws: Any) -> None:
    aws.put_object(Bucket="raw-bucket", Key=LOCK_KEY, Body=_lock_body(NOW - timedelta(minutes=16)))
    summary = handler.lambda_handler({}, None)
    assert summary["dbt_status"] == "success"
    assert "Contents" not in aws.list_objects_v2(Bucket="raw-bucket", Prefix="state/boston/")


def test_lock_exactly_at_the_stale_limit_is_still_held(aws: Any) -> None:
    aws.put_object(Bucket="raw-bucket", Key=LOCK_KEY, Body=_lock_body(NOW - timedelta(minutes=15)))
    assert not handler.RunLock(aws, "raw-bucket", LOCK_KEY, lambda: NOW).acquire()


def test_only_one_of_two_runs_replaces_a_stale_lock(aws: Any) -> None:
    aws.put_object(Bucket="raw-bucket", Key=LOCK_KEY, Body=_lock_body(NOW - timedelta(hours=1)))
    first = handler.RunLock(aws, "raw-bucket", LOCK_KEY, lambda: NOW)
    second = handler.RunLock(aws, "raw-bucket", LOCK_KEY, lambda: NOW)
    assert first.acquire()
    assert not second.acquire()  # the first run's fresh lock now holds
    second.release()  # not the owner: leaves the lock alone
    assert aws.head_object(Bucket="raw-bucket", Key=LOCK_KEY)
    first.release()
    assert "Contents" not in aws.list_objects_v2(Bucket="raw-bucket", Prefix="state/boston/")


def test_unreadable_lock_ages_by_its_last_modified_time(aws: Any) -> None:
    aws.put_object(Bucket="raw-bucket", Key=LOCK_KEY, Body=b"not json")
    fresh = handler.RunLock(aws, "raw-bucket", LOCK_KEY, lambda: datetime.now(UTC))
    assert not fresh.acquire()
    later = handler.RunLock(
        aws, "raw-bucket", LOCK_KEY, lambda: datetime.now(UTC) + timedelta(minutes=20)
    )
    assert later.acquire()


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


def _set_chaos(mode: str, minutes: int) -> None:
    until = (NOW + timedelta(minutes=minutes)).isoformat()
    boto3.client("ssm").put_parameter(
        Name="/headway/chaos",
        Type="String",
        Overwrite=True,
        Value=json.dumps({"mode": mode, "until": until}),
    )


def test_chaos_md_down_fails_the_publish_after_gold_is_exported(
    fakes: dict[str, Any], aws: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    _set_chaos("md_down", 15)
    with pytest.raises(handler.ChaosError, match="md_down"):
        handler.lambda_handler({}, None)
    logged = _summary(caplog)
    assert "ChaosError" in logged["publish_error"] and logged["gold_exported"] is True
    assert fakes["published"] == []
    assert any("CHAOS ACTIVE mode=md_down" in r.message for r in caplog.records)
    (row,) = _runs(aws)
    assert row["status"] == "failed" and row["exported"] and not row["gold_published"]


def test_chaos_after_until_or_for_another_mode_leaves_the_publish_alone(
    fakes: dict[str, Any], aws: Any
) -> None:
    _set_chaos("md_down", -1)
    handler.lambda_handler({}, None)
    assert len(fakes["published"]) == 1
    _set_chaos("feed_down", 15)
    handler.lambda_handler({}, None)
    assert len(fakes["published"]) == 2


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


def test_r2_models_are_built_only_when_enabled(
    fakes: dict[str, Any], aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    handler.lambda_handler({}, None)
    _, build, test, _ = fakes["dbt"]
    assert "tag:r2" not in build and "tag:r2" not in test
    assert build[:4] == ["build", "--exclude", "resource_type:test", "tag:s3_load"]
    assert test[0] == "test"


def test_r2_exclusion_helper() -> None:
    import os

    os.environ.pop("HEADWAY_ENABLE_R2", None)
    os.environ.pop("HEADWAY_ENABLE_R3", None)
    os.environ.pop("HEADWAY_ENABLE_R4", None)
    assert handler.r2_exclusions() == ["tag:r2", "tag:r3", "tag:r4"]


def test_r3_models_are_built_only_when_enabled_with_r2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    monkeypatch.delenv("HEADWAY_ENABLE_R3", raising=False)
    monkeypatch.delenv("HEADWAY_ENABLE_R4", raising=False)
    assert handler.r2_exclusions() == ["tag:r3", "tag:r4"]
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "true")
    assert handler.r2_exclusions() == ["tag:r4"]
    # R3 reads R2 models: without R2 both stay out
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "false")
    assert handler.r2_exclusions() == ["tag:r2", "tag:r3", "tag:r4"]


def test_r4_models_are_built_only_when_enabled_with_r2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "true")
    monkeypatch.delenv("HEADWAY_ENABLE_R4", raising=False)
    assert handler.r2_exclusions() == ["tag:r4"]  # R4 off: production is unchanged
    monkeypatch.setenv("HEADWAY_ENABLE_R4", "false")
    assert handler.r2_exclusions() == ["tag:r4"]
    monkeypatch.setenv("HEADWAY_ENABLE_R4", "true")
    assert handler.r2_exclusions() == []
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "false")  # R4 does not depend on R3
    assert handler.r2_exclusions() == ["tag:r3"]
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "false")  # but it reads R2 models
    assert handler.r2_exclusions() == ["tag:r2", "tag:r3", "tag:r4"]


def test_r4_site_tables_are_exported_only_with_r2_and_r4(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "true")
    monkeypatch.delenv("HEADWAY_ENABLE_R4", raising=False)
    before = publish.gold_tables()
    assert not set(publish.R4_GOLD_TABLES) & set(before)
    assert before == publish.GOLD_TABLES + publish.R2_GOLD_TABLES + publish.R3_GOLD_TABLES
    monkeypatch.setenv("HEADWAY_ENABLE_R4", "true")
    assert publish.gold_tables() == before + publish.R4_GOLD_TABLES
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "false")
    assert not set(publish.R4_GOLD_TABLES) & set(publish.gold_tables())


def test_r3_forecast_tables_are_exported_only_with_r2_and_r3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "false")
    assert not set(publish.R3_GOLD_TABLES) & set(publish.gold_tables())
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "true")
    assert set(publish.R3_GOLD_TABLES) <= set(publish.gold_tables())
    # the backtest is conditional: never in the always-exported set
    assert not set(publish.R3_OPTIONAL_TABLES) & set(publish.gold_tables())
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "false")
    assert not {t for t in publish.gold_tables() if "forecast" in t}


def test_gold_tables_include_release_2_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADWAY_ENABLE_R2", raising=False)
    assert publish.gold_tables() == publish.GOLD_TABLES
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    assert publish.gold_tables() == publish.GOLD_TABLES + publish.R2_GOLD_TABLES
    assert "agg_ett_daily" in publish.gold_tables()
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "false")
    assert not set(publish.R2_GOLD_TABLES) & set(publish.gold_tables())


# ---------------------------------------------------------------- history (Release 2)


def _r2_database(path: str) -> None:
    con = duckdb.connect(path)
    con.execute("create table agg_ett_daily (service_date date, line varchar, n bigint)")
    con.execute(
        "insert into agg_ett_daily values ('2026-11-20', 'Red', 1), ('2026-11-22', 'Red', 2)"
    )
    con.close()


def test_r2_run_passes_history_vars_and_writes_closed_days(
    fakes: dict[str, Any], aws: Any, monkeypatch: pytest.MonkeyPatch, pipeline_db: str
) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    _r2_database(pipeline_db)
    monkeypatch.setattr(handler, "_now", lambda: datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    monkeypatch.setattr(handler.cost, "write_cost_daily", lambda *a, **k: None)
    summary = handler.lambda_handler({}, None)

    build = fakes["dbt"][1]
    variables = json.loads(build[build.index("--vars") + 1])
    assert variables["history_root"] == "s3://public-bucket/public/boston/history"
    assert variables["metric_versions"] == history.table_versions()
    assert variables["slo_as_of"] == "2026-11-23T11:30:00Z"  # now (12:00) minus 30 minutes
    keys = [
        o["Key"]
        for o in aws.list_objects_v2(Bucket="public-bucket", Prefix="public/boston/history")[
            "Contents"
        ]
    ]
    assert keys == [
        "public/boston/history/table=agg_ett_daily/metric_version=2/service_date=2026-11-20/part.parquet",
        "public/boston/history/table=agg_ett_daily/metric_version=2/service_date=2026-11-22/part.parquet",
    ]  # fmt: skip
    assert summary["history_written"] == 2
    assert fakes["history_publish"] == {
        "history_url": "s3://public-bucket/public/boston/history",
        "history_tables": list(history.HISTORY_TABLES),
    }


def test_flag_off_has_no_history(fakes: dict[str, Any], aws: Any) -> None:
    summary = handler.lambda_handler({}, None)
    assert "history_written" not in summary
    assert "Contents" not in aws.list_objects_v2(
        Bucket="public-bucket", Prefix="public/boston/history"
    )
    assert fakes["history_publish"] == {}


def test_rebuild_history_is_refused_without_the_flag_or_with_a_bad_payload(
    fakes: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    event = {
        "task": "rebuild_history",
        "from": "2026-11-10",
        "to": "2026-11-12",
        "metric_version": 2,
    }
    with pytest.raises(handler.TransformError, match="HEADWAY_ENABLE_R2"):
        handler.lambda_handler(event, None)
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    with pytest.raises(ValueError, match="matches no family"):
        handler.lambda_handler({**event, "metric_version": 9}, None)
    with pytest.raises(handler.TransformError, match="unknown task"):
        handler.lambda_handler({"task": "bogus"}, None)
    assert fakes["dbt"] == []  # nothing was built


def test_rebuild_history_backfills_only_missing_days_and_leaves_gold_alone(
    fakes: dict[str, Any], aws: Any, monkeypatch: pytest.MonkeyPatch, pipeline_db: str
) -> None:
    monkeypatch.setenv("HEADWAY_ENABLE_R2", "true")
    _r2_database(pipeline_db)
    monkeypatch.setattr(handler, "_now", lambda: datetime(2026, 11, 23, 12, 0, tzinfo=UTC))
    existing = (
        "public/boston/history/table=agg_ett_daily/metric_version=2/"
        "service_date=2026-11-20/part.parquet"
    )
    aws.put_object(Bucket="public-bucket", Key=existing, Body=b"original")
    event = {
        "task": "rebuild_history",
        "from": "2026-11-20",
        "to": "2026-11-22",
        "metric_version": 2,
    }
    summary = handler.lambda_handler(event, None)
    assert summary["written"] == {"agg_ett_daily": ["2026-11-22"]}
    assert summary["history_existing"] == 1
    assert aws.get_object(Bucket="public-bucket", Key=existing)["Body"].read() == b"original"
    assert [a[0] for a in fakes["dbt"]] == ["run", "build", "test"]  # no export_gold
    variables = json.loads(fakes["dbt"][1][fakes["dbt"][1].index("--vars") + 1])
    assert variables["lookback_days"] == 7  # at least the default, reaching the 20th
    assert fakes["history_publish"]["history_tables"] == list(history.HISTORY_TABLES)


# ---------------------------------------------------------------- persisted run state


def _runs(aws: Any) -> list[dict[str, Any]]:
    keys = [
        o["Key"]
        for o in aws.list_objects_v2(Bucket="raw-bucket", Prefix="state/transform_runs/").get(
            "Contents", []
        )
    ]
    if not keys:
        return []
    con = duckdb.connect()
    con.execute("set TimeZone = 'UTC'")
    rows: list[dict[str, Any]] = []
    for key in keys:
        path = f"/tmp/{key.replace('/', '_')}"  # noqa: S108
        Path(path).write_bytes(aws.get_object(Bucket="raw-bucket", Key=key)["Body"].read())
        cur = con.execute(
            "select run_id, task, status, tests_passed, tests_failed, rows_exported, "
            "gold_exported_at is not null as exported, gold_published, error, "
            f"strftime(slot, '%Y-%m-%dT%H:%M') as slot from read_parquet('{path}')"
        )
        names = [d[0] for d in cur.description]
        rows += [dict(zip(names, r, strict=True)) for r in cur.fetchall()]
    return rows


def test_a_successful_run_is_recorded_for_the_next_run(fakes: dict[str, Any], aws: Any) -> None:
    handler.lambda_handler({}, None)
    (row,) = _runs(aws)
    assert row["task"] == "scheduled" and row["status"] == "success"
    assert row["tests_passed"] == 9 and row["tests_failed"] == 0 and row["rows_exported"] == 12
    assert row["exported"] and row["gold_published"] and row["error"] is None
    assert row["slot"] == "2026-11-22T07:00"  # the 07:20 run belongs to the 07:00 slot


def test_a_run_with_failed_tests_is_recorded_as_such(fakes: dict[str, Any], aws: Any) -> None:
    fakes["outcome"] = handler.DbtOutcome(False, tests_passed=8, tests_failed=2)
    handler.lambda_handler({}, None)
    (row,) = _runs(aws)
    assert row["status"] == "tests_failed" and row["tests_failed"] == 2 and row["gold_published"]


def test_a_failed_run_is_recorded_with_its_error(fakes: dict[str, Any], aws: Any) -> None:
    fakes["skip_parquet"] = True
    with pytest.raises(handler.ExportError):
        handler.lambda_handler({}, None)
    (row,) = _runs(aws)
    assert row["status"] == "failed" and not row["exported"] and not row["gold_published"]
    assert "ExportError" in row["error"]


def test_a_publish_failure_is_recorded_as_a_failed_run_with_gold_exported(
    fakes: dict[str, Any], aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*a: Any, **k: Any) -> list[str]:
        raise RuntimeError("md down")

    monkeypatch.setattr(handler.publish, "publish_motherduck", boom)
    with pytest.raises(RuntimeError):
        handler.lambda_handler({}, None)
    (row,) = _runs(aws)
    assert row["status"] == "failed" and row["exported"] and not row["gold_published"]


def test_a_skipped_locked_run_writes_no_record(fakes: dict[str, Any], aws: Any) -> None:
    aws.put_object(
        Bucket="raw-bucket", Key="state/boston/transform.lock",
        Body=json.dumps({"run_id": "x", "started_at": NOW.isoformat()}),
    )  # fmt: skip
    assert handler.lambda_handler({}, None) == {"skipped": "locked"}
    assert _runs(aws) == []


def test_state_is_loaded_before_dbt_runs(
    fakes: dict[str, Any], aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []
    real = handler.state.load
    monkeypatch.setattr(
        handler.state, "load", lambda *a, **k: (order.append("load"), real(*a, **k))[1]
    )
    real_dbt = handler.run_dbt
    monkeypatch.setattr(handler, "run_dbt", lambda args: (order.append(args[0]), real_dbt(args))[1])
    handler.lambda_handler({}, None)
    assert order[:2] == ["load", "run"]
