"""AWS Lambda entry point: dbt build, gold export, MotherDuck publish, daily cost report."""

import json
import logging
import os
import time
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import boto3

from headway_transform import cost, publish

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_LOOKBACK_DAYS = 7
TOKEN_PARAMETER = "/headway/motherduck_token"
DBT_TARGET_PATH = "/tmp/dbt_target"  # noqa: S108
DBT_LOG_PATH = "/tmp/dbt_logs"  # noqa: S108


class TransformError(Exception):
    """dbt could not build the models, so there is nothing to export."""


@dataclass
class DbtOutcome:
    success: bool
    tests_passed: int = 0
    tests_failed: int = 0
    node_errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


def _dbt_flags() -> list[str]:
    project = os.environ.get("DBT_PROJECT_DIR", "/var/task/dbt_project")
    return [
        "--project-dir", project,
        "--profiles-dir", project,
        "--target", "lambda",
        "--target-path", DBT_TARGET_PATH,
        "--log-path", DBT_LOG_PATH,
    ]  # fmt: skip


def _use_thread_locks_without_semaphores() -> None:
    """Lambda has no /dev/shm, so POSIX semaphores (sem_open) fail with ENOENT.

    dbt builds its connection-manager lock via ``get_mp_context().RLock()``, which resolves to
    ``multiprocessing.synchronize``. We run single-process (threads only), so thread locks are
    equivalent. Only patches when a semaphore genuinely cannot be created.
    """
    import multiprocessing
    import multiprocessing.synchronize as synchronize
    import threading

    try:
        multiprocessing.get_context("spawn").RLock()
        return
    except OSError:
        logger.warning("multiprocessing semaphores unavailable; using thread locks for dbt")

    def thread_lock(*, ctx: object = None) -> Any:
        return threading.Lock()

    def thread_rlock(*, ctx: object = None) -> Any:
        return threading.RLock()

    synchronize.Lock = thread_lock  # type: ignore[misc,assignment]
    synchronize.RLock = thread_rlock  # type: ignore[misc,assignment]


def run_dbt(args: list[str]) -> DbtOutcome:
    _use_thread_locks_without_semaphores()
    from dbt.cli.main import dbtRunner  # imported lazily: dbt lives in the image env only

    res = dbtRunner().invoke([*args, *_dbt_flags()])
    if res.exception is not None:
        logger.error(
            "dbt %s raised:\n%s",
            args[0],
            "".join(traceback.format_exception(res.exception)),
        )
        raise TransformError(f"dbt {args[0]} failed: {res.exception}")
    outcome = DbtOutcome(success=bool(res.success))
    results = getattr(res.result, "results", None) or []
    for r in results:
        if getattr(r, "node", None) is None:  # run-operation results carry no node
            continue
        status = str(getattr(r.status, "value", r.status))
        is_test = str(getattr(r.node.resource_type, "value", r.node.resource_type)) == "test"
        if is_test and status in ("fail", "error"):
            outcome.tests_failed += 1
        elif is_test and status in ("pass", "warn"):
            outcome.tests_passed += 1
        elif not is_test and status == "error":
            outcome.node_errors.append(str(r.node.name))
    return outcome


def _tables_to_publish(s3: Any, bucket: str, gold_prefix: str) -> list[str]:
    tables = list(publish.GOLD_TABLES)
    for name in publish.OPTIONAL_TABLES:
        try:
            s3.head_object(Bucket=bucket, Key=f"{gold_prefix}/{name}.parquet")
        except s3.exceptions.ClientError:
            continue
        tables.append(name)
    return tables


def _smoke(started: float) -> dict[str, Any]:
    """Image smoke test: dbt build + gold export on a local curated root; no AWS, no publish."""
    root = os.environ["HEADWAY_SMOKE_CURATED_ROOT"]
    gold = os.environ["HEADWAY_SMOKE_GOLD_DIR"]
    os.makedirs(gold, exist_ok=True)
    variables = json.dumps(
        {
            "curated_root": root,
            "city_prefix": os.environ["CITY_ID"],
            "lookback_days": int(os.environ.get("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
        }
    )
    build = run_dbt(["build", "--vars", variables])
    if build.node_errors:
        raise TransformError(f"dbt models failed: {', '.join(build.node_errors)}")
    run_dbt(["run-operation", "export_gold", "--vars", variables,
             "--args", json.dumps({"target_dir": gold})])  # fmt: skip
    with open(f"{gold}/manifest.json") as handle:
        counts = json.load(handle)["row_counts"]
    summary = {
        "smoke": True,
        "dbt_status": "success" if build.success else "tests_failed",
        "tests_failed": build.tests_failed,
        "tests_passed": build.tests_passed,
        "rows_exported": sum(counts.values()),
        "tables_exported": len(counts),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    logger.info(json.dumps(summary, sort_keys=True))
    return summary


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    started = time.monotonic()
    os.environ.setdefault("HOME", "/tmp")  # noqa: S108  Lambda leaves HOME unset
    if os.environ.get("HEADWAY_SMOKE_GOLD_DIR"):
        return _smoke(started)
    raw_bucket = os.environ["RAW_BUCKET"]
    public_bucket = os.environ["PUBLIC_BUCKET"]
    city = os.environ["CITY_ID"]
    lookback = int(os.environ.get("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    gold_prefix = f"public/{city}/gold"
    gold_url = f"s3://{public_bucket}/{gold_prefix}"
    db_path = os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108
    s3 = boto3.client("s3")
    steps: dict[str, int] = {}
    summary: dict[str, Any] = {}

    def timed(name: str, t0: float) -> None:
        steps[name] = round((time.monotonic() - t0) * 1000)

    t0 = time.monotonic()
    variables = json.dumps(
        {"curated_root": f"s3://{raw_bucket}", "city_prefix": city, "lookback_days": lookback}
    )
    build = run_dbt(["build", "--vars", variables])
    timed("dbt_build", t0)
    if build.node_errors:
        raise TransformError(f"dbt models failed: {', '.join(build.node_errors)}")
    if build.tests_failed:
        logger.warning("dbt tests failed: %d (export continues)", build.tests_failed)

    t0 = time.monotonic()
    run_dbt(["run-operation", "export_gold", "--vars", variables,
             "--args", json.dumps({"target_dir": gold_url})])  # fmt: skip
    timed("export_gold", t0)
    manifest = json.loads(
        s3.get_object(Bucket=public_bucket, Key=f"{gold_prefix}/manifest.json")["Body"].read()
    )
    rows_exported = manifest["row_counts"]

    cost_rows: int | None = None
    t0 = time.monotonic()
    try:
        cost_rows = cost.write_cost_daily(
            s3, public_bucket, gold_prefix, boto3.client("ce", region_name="us-east-1"),
            db_path, _now(),
        )  # fmt: skip
    except Exception as exc:
        logger.exception("cost report failed")
        summary["cost_error"] = f"{type(exc).__name__}: {exc}"
    timed("cost", t0)

    publish_error: Exception | None = None
    t0 = time.monotonic()
    try:
        token = boto3.client("ssm").get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)[
            "Parameter"
        ]["Value"]
        published = publish.publish_motherduck(
            token, gold_url, _tables_to_publish(s3, public_bucket, gold_prefix)
        )
        summary["published_tables"] = len(published)
    except Exception as exc:
        logger.exception("MotherDuck publish failed")
        publish_error = exc
        summary["publish_error"] = f"{type(exc).__name__}: {exc}"
    timed("publish", t0)

    summary.update(
        {
            "dbt_status": "success" if build.success else "tests_failed",
            "tests_failed": build.tests_failed,
            "tests_passed": build.tests_passed,
            "rows_exported": sum(rows_exported.values()),
            "tables_exported": len(rows_exported),
            "cost_rows": cost_rows,
            "step_ms": steps,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
    )
    logger.info(json.dumps(summary, sort_keys=True, default=str))
    if publish_error is not None:
        raise publish_error
    return summary
