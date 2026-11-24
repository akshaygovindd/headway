from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pytest
from moto import mock_aws

from headway_transform import handler, observability

NOW = datetime(2026, 11, 23, 7, 20, tzinfo=UTC)


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "RAW_BUCKET": "raw-bucket",
        "PUBLIC_BUCKET": "public-bucket",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="public-bucket")
        yield s3


def _elementary_db(path: str, tests: list[tuple[str, str]], metrics: list[str], at: str) -> None:
    """A DuckDB file with the Elementary tables as dbt creates them (all columns, naive UTC)."""
    con = duckdb.connect(path)
    for table in (observability.ANOMALY_TEST_RESULTS, observability.DATA_MONITORING_METRICS):
        con.execute(table.ddl.replace("create or replace table", "create table if not exists"))
    for i, (test_type, name) in enumerate(tests):
        con.execute(
            "insert into main.elementary_test_results (id, test_type, test_name, status, "
            "detected_at, created_at) values "
            f"(?, ?, ?, 'pass', timestamp '{at}', timestamp '{at}')",
            [f"r{i}", test_type, name],
        )
    for m in metrics:
        con.execute(
            "insert into main.data_monitoring_metrics (id, metric_name, metric_value, created_at) "
            f"values (?, 'row_count', 5.0, timestamp '{at}')",
            [m],
        )
    con.close()


def _count(path: str, table: str) -> int:
    con = duckdb.connect(path, read_only=True)
    try:
        return int(con.execute(f"select count(*) from main.{table}").fetchone()[0])  # type: ignore[index]  # noqa: S608
    finally:
        con.close()


def test_persist_keeps_only_this_runs_anomaly_rows_and_restore_puts_them_back(
    aws: Any, tmp_path: Path
) -> None:
    first = str(tmp_path / "run1.duckdb")
    _elementary_db(
        first,
        [("anomaly_detection", "volume"), ("dbt_test", "not_null"), ("dbt_test", "unique")],
        ["m1", "m2"],
        "2026-11-23 07:21:00",
    )
    written = observability.persist(aws, "raw-bucket", first, NOW, NOW + timedelta(minutes=2))
    assert written == {"elementary_test_results": 1, "data_monitoring_metrics": 2}

    # the next container: a fresh file whose Elementary tables are built empty, then restored
    second = str(tmp_path / "run2.duckdb")
    _elementary_db(second, [], [], "2026-11-23 00:00:00")
    restored = observability.restore(aws, "raw-bucket", second, NOW + timedelta(hours=1))
    assert restored == {"elementary_test_results": 1, "data_monitoring_metrics": 2}


def test_persist_ignores_rows_older_than_the_run(aws: Any, tmp_path: Path) -> None:
    db = str(tmp_path / "old.duckdb")
    _elementary_db(db, [("anomaly_detection", "volume")], ["m1"], "2026-11-22 07:21:00")
    written = observability.persist(aws, "raw-bucket", db, NOW, NOW)
    assert written == {"elementary_test_results": 0, "data_monitoring_metrics": 0}
    assert aws.list_objects_v2(Bucket="raw-bucket", Prefix="state/").get("KeyCount") == 0


def test_restore_creates_the_tables_when_dbt_has_not(aws: Any, tmp_path: Path) -> None:
    db = str(tmp_path / "bare.duckdb")
    duckdb.connect(db).close()
    assert observability.restore(aws, "raw-bucket", db, NOW) == {
        "elementary_test_results": 0,
        "data_monitoring_metrics": 0,
    }


def test_the_report_is_due_once_a_day_after_six_utc(aws: Any) -> None:
    assert observability.report_due(aws, "public-bucket", "boston", NOW)
    assert not observability.report_due(aws, "public-bucket", "boston", NOW.replace(hour=5))
    aws.put_object(Bucket="public-bucket", Key=observability.report_key("boston"), Body=b"<html>")
    today = datetime.now(UTC).replace(hour=23)  # moto stamps objects with the real clock
    assert not observability.report_due(aws, "public-bucket", "boston", today)
    assert observability.report_due(aws, "public-bucket", "boston", today + timedelta(days=1))


def test_publish_uploads_the_generated_report(
    aws: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_generate(db_path: str, out: Path, work: Path) -> Path:
        out.write_text("<html>report</html>")
        return out

    monkeypatch.setattr(observability, "generate_report", fake_generate)
    key = observability.publish_report(aws, "public-bucket", "boston", "x.duckdb", NOW)
    assert key == "public/boston/observability/index.html"
    head = aws.head_object(Bucket="public-bucket", Key=key)
    assert head["ContentType"].startswith("text/html") and head["CacheControl"] == "max-age=300"


def test_edr_runs_without_telemetry_and_with_everything_under_the_work_dir(tmp_path: Path) -> None:
    args = observability._edr_args("/proj", "/prof", tmp_path, tmp_path / "index.html")
    assert args[0] == "report"
    assert args[args.index("--open-browser") + 1] == "false"
    for flag in ("--config-dir", "--target-path", "--file-path"):
        assert args[args.index(flag) + 1].startswith(str(tmp_path))
    assert "anonymous_usage_tracking: false" in (tmp_path / "config" / "config.yml").read_text()


def test_a_failing_report_is_logged_and_never_fails_the_run(
    aws: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db = str(tmp_path / "e.duckdb")
    _elementary_db(db, [("anomaly_detection", "volume")], ["m1"], "2026-11-23 07:21:00")

    def boom(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("edr exploded")

    monkeypatch.setattr(handler, "_now", lambda: NOW + timedelta(minutes=2))
    monkeypatch.setattr(handler.observability, "publish_report", boom)
    out = handler._publish_observability(aws, "public-bucket", "boston", db, NOW)
    assert out["elementary_persisted"] == {
        "elementary_test_results": 1,
        "data_monitoring_metrics": 1,
    }
    assert out["observability_error"] == "RuntimeError: edr exploded"
