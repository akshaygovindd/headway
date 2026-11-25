"""Elementary observability (ADR-018): result tables that outlive the container, and a daily report.

The Elementary dbt package writes its tables into the run's DuckDB file (schema ``main``, built
with the other models). A fresh /tmp loses them, so the two that carry history are persisted with
the run-state mechanism in `state.py`: anomaly test results (the test history the report shows)
and the data-monitoring metrics (the baseline every anomaly test compares against). They are
restored after the models are built and before the tests run, and the rows this run created are
appended at the end. The other Elementary tables are rebuilt from dbt's own artifacts each run.

Once a day, on the first run at or after 06:00 UTC, the `edr` CLI renders its HTML report from the
DuckDB file and it is uploaded to ``public/<city>/observability/index.html``. `edr` runs in-process
with its internal dbt package already installed in the image (nothing is downloaded), its logs and
cache under /tmp, and telemetry off. A failure here never fails the run.
"""

import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from headway_transform import cost, duckdb_limits, state

logger = logging.getLogger()

REPORT_NAME = "index.html"
REPORT_CACHE_CONTROL = "max-age=300"

_RESULT_COLUMNS = {
    "id": "varchar",
    "data_issue_id": "varchar",
    "test_execution_id": "varchar",
    "test_unique_id": "varchar",
    "model_unique_id": "varchar",
    "invocation_id": "varchar",
    "detected_at": "timestamp",
    "created_at": "timestamp",
    "database_name": "varchar",
    "schema_name": "varchar",
    "table_name": "varchar",
    "column_name": "varchar",
    "test_type": "varchar",
    "test_sub_type": "varchar",
    "test_results_description": "varchar",
    "owners": "varchar",
    "tags": "varchar",
    "test_results_query": "varchar",
    "other": "varchar",
    "test_name": "varchar",
    "test_params": "varchar",
    "severity": "varchar",
    "status": "varchar",
    "failures": "bigint",
    "test_short_name": "varchar",
    "test_alias": "varchar",
    "result_rows": "varchar",
    "failed_row_count": "bigint",
}
_METRIC_COLUMNS = {
    "id": "varchar",
    "full_table_name": "varchar",
    "column_name": "varchar",
    "metric_name": "varchar",
    "metric_type": "varchar",
    "metric_value": "float",
    "source_value": "varchar",
    "bucket_start": "timestamp",
    "bucket_end": "timestamp",
    "bucket_duration_hours": "integer",
    "updated_at": "timestamp",
    "dimension": "varchar",
    "dimension_value": "varchar",
    "metric_properties": "varchar",
    "created_at": "timestamp",
}

# Only the anomaly detection rows: the ~600 plain dbt test rows of every run are rebuilt from the
# run itself and would grow the persisted history by tens of thousands of rows a day.
ANOMALY_TEST_RESULTS = state.StateTable(
    name="elementary_test_results",
    folder="elementary_test_results",
    timestamp="detected_at",
    columns=_RESULT_COLUMNS,
)
DATA_MONITORING_METRICS = state.StateTable(
    name="data_monitoring_metrics",
    folder="elementary_data_monitoring_metrics",
    timestamp="created_at",
    columns=_METRIC_COLUMNS,
)
# (table, filter on the rows to keep) in the order they are restored and persisted
PERSISTED: tuple[tuple[state.StateTable, str], ...] = (
    (ANOMALY_TEST_RESULTS, "test_type = 'anomaly_detection'"),
    (DATA_MONITORING_METRICS, "true"),
)


def restore(s3: Any, bucket: str, db_path: str, now: datetime) -> dict[str, int]:
    """Put the persisted history into the Elementary tables dbt has just created."""
    return {t.name: state.restore(s3, bucket, db_path, t, now) for t, _ in PERSISTED}


def persist(s3: Any, bucket: str, db_path: str, since: datetime, now: datetime) -> dict[str, int]:
    """Append the rows this run created (stamped at or after ``since``) to the persisted history."""
    cutoff = since.astimezone(UTC).replace(tzinfo=None)  # Elementary stamps naive UTC
    written: dict[str, int] = {}
    con = duckdb_limits.connect(db_path, read_only=True)
    try:
        con.execute("set TimeZone = 'UTC'")
        for table, keep in PERSISTED:
            cols = ", ".join(table.columns)
            rows = con.execute(
                f"select {cols} from main.{table.name} "  # noqa: S608
                f"where {table.timestamp} >= ? and {keep}",
                [cutoff],
            ).fetchall()
            written[table.name] = len(rows)
            if rows:
                state.append(s3, bucket, table, rows, now)
    finally:
        con.close()
    return written


def report_key(city: str) -> str:
    return f"public/{city}/observability/{REPORT_NAME}"


def report_due(s3: Any, bucket: str, city: str, now: datetime) -> bool:
    """Once per UTC day, on the first run at or after 06:00 UTC (the cost report's rule)."""
    try:
        last = s3.head_object(Bucket=bucket, Key=report_key(city))["LastModified"]
    except s3.exceptions.ClientError:
        last = None
    return cost.due(last, now)


def _edr_args(project_dir: str, profiles_dir: str, work: Path, out: Path) -> list[str]:
    config_dir = work / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        "anonymous_usage_tracking: false\ndisable_elementary_logo_print: true\n"
    )
    return [
        "report",
        "--project-dir", project_dir,
        "--profiles-dir", profiles_dir,
        "--config-dir", str(config_dir),
        "--target-path", str(work / "edr_target"),
        "--file-path", str(out),
        "--open-browser", "false",
        "--disable-samples", "true",
        "--quiet-logs",
    ]  # fmt: skip


def generate_report(db_path: str, out: Path, work: Path) -> Path:
    """Render the Elementary report for the DuckDB file at ``db_path`` into ``out``.

    Runs `edr report` in-process (dbt is already an in-process library here), so the caller must
    have released dbt's own connection to the file first.
    """
    project_dir = os.environ.get("DBT_PROJECT_DIR", "/var/task/dbt_project")
    os.environ["HEADWAY_DUCKDB_PATH"] = db_path
    os.environ["DBT_TARGET_PATH"] = str(work / "dbt_target")
    os.environ["DBT_LOG_PATH"] = str(work / "edr_target")
    os.environ["DBT_SEND_ANONYMOUS_USAGE_STATS"] = "False"
    from headway_transform import handler  # late: handler imports this module

    handler._use_thread_locks_without_semaphores()  # noqa: SLF001  Lambda has no /dev/shm
    args = _edr_args(project_dir, project_dir, work, out)
    # edr fixes its default target directory (./edr_target) when it is imported and creates it then;
    # /var/task is read-only, so the import happens from the work directory, and a warm container
    # that imported it for an earlier (deleted) work directory gets the new one set explicitly.
    previous_cwd = os.getcwd()
    os.chdir(work)
    try:
        from elementary.cli.cli import cli  # type: ignore[import-not-found]  # image env only
        from elementary.config.config import Config  # type: ignore[import-not-found]

        Config.DEFAULT_TARGET_PATH = str(work / "edr_target")
        Config.DEFAULT_CONFIG_DIR = str(work / "config")
        cli.main(args, standalone_mode=False, prog_name="edr")
    except SystemExit as exc:  # click exits 0 on success when it is not in standalone mode
        if exc.code not in (0, None):
            raise RuntimeError(f"edr report exited with {exc.code}") from exc
    finally:
        os.chdir(previous_cwd)
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError("edr report produced no file")
    return out


def publish_report(s3: Any, public_bucket: str, city: str, db_path: str, now: datetime) -> str:
    """Generate the report and upload it; returns its key."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        out = generate_report(db_path, work / REPORT_NAME, work)
        key = report_key(city)
        s3.put_object(
            Bucket=public_bucket,
            Key=key,
            Body=out.read_bytes(),
            ContentType="text/html; charset=utf-8",
            CacheControl=REPORT_CACHE_CONTROL,
        )
    logger.info("observability report written to s3://%s/%s", public_bucket, key)
    return key
