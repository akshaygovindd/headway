"""AWS Lambda entry point: dbt build, gold export, MotherDuck publish, daily cost report."""

import json
import logging
import os
import shutil
import time
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3

from headway_transform import chaos, cost, history, publish, state

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_LOOKBACK_DAYS = 7
DEFAULT_TIMEZONE = "America/New_York"
SLO_LAG = timedelta(minutes=30)
TOKEN_PARAMETER = "/headway/motherduck_token"
DBT_TARGET_PATH = "/tmp/dbt_target"  # noqa: S108
DBT_LOG_PATH = "/tmp/dbt_logs"  # noqa: S108
# A lock older than this belongs to a run that crashed or timed out (the Lambda limit is 900 s).
LOCK_STALE_AFTER = timedelta(minutes=15)
_LOCK_CONFLICT = {"PreconditionFailed", "ConditionalRequestConflict"}


RUNTIME_EXTENSION_DIR = "/tmp/duckdb_extensions"  # noqa: S108


class TransformError(Exception):
    """dbt could not build the models, so there is nothing to export."""


class ChaosError(Exception):
    """The md_down drill (ADR-017): the MotherDuck publish is made to fail on purpose."""


class ExportError(TransformError):
    """export_gold ran but the expected gold objects are not in the bucket."""


@dataclass
class RunRecord:
    """What one invocation writes to agg_transform_runs. `status` stays 'failed' until the run
    reaches its end, so an exception anywhere is recorded as a failure."""

    run_id: str
    task: str
    started_at: datetime
    status: str = "failed"
    tests_passed: int | None = None
    tests_failed: int | None = None
    rows_exported: int | None = None
    gold_exported_at: datetime | None = None
    gold_published: bool = False
    steps: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def row(self, finished_at: datetime) -> tuple[Any, ...]:
        return (
            self.run_id, self.task, state.slot_of(self.started_at), self.started_at, finished_at,
            self.status, self.tests_passed, self.tests_failed, self.rows_exported,
            self.gold_exported_at, self.gold_published, publish.r2_enabled(),
            round((finished_at - self.started_at).total_seconds() * 1000),
            json.dumps(self.steps, sort_keys=True), self.error,
        )  # fmt: skip


@dataclass
class DbtOutcome:
    success: bool
    tests_passed: int = 0
    tests_failed: int = 0
    node_errors: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(UTC)


class RunLock:
    """Single-run guard: an S3 object created with a conditional put (``IfNoneMatch='*'``).

    Another run holding a fresh lock makes ``acquire`` return False. A lock older than
    ``LOCK_STALE_AFTER`` is replaced with ``IfMatch`` on the stale object's ETag, so two runs that
    both find it stale cannot both win.
    """

    def __init__(self, s3: Any, bucket: str, key: str, now: Any = None) -> None:
        self.s3 = s3
        self.bucket = bucket
        self.key = key
        self.now = now or _now
        self.run_id = uuid.uuid4().hex

    def _body(self) -> bytes:
        return json.dumps({"run_id": self.run_id, "started_at": self.now().isoformat()}).encode()

    def _put(self, **condition: str) -> bool:
        try:
            self.s3.put_object(
                Bucket=self.bucket, Key=self.key, Body=self._body(),
                ContentType="application/json", **condition,
            )  # fmt: skip
        except self.s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in _LOCK_CONFLICT | {"NoSuchKey"}:
                return False
            raise
        return True

    def acquire(self) -> bool:
        for _ in range(2):  # the second pass covers a holder releasing between our two calls
            if self._put(IfNoneMatch="*"):
                return True
            try:
                held = self.s3.get_object(Bucket=self.bucket, Key=self.key)
            except self.s3.exceptions.NoSuchKey:
                continue
            started = self._started_at(held)
            if self.now() - started <= LOCK_STALE_AFTER:
                return False
            logger.warning("replacing stale transform lock from %s", started.isoformat())
            return self._put(IfMatch=held["ETag"])
        return False

    @staticmethod
    def _started_at(held: dict[str, Any]) -> datetime:
        try:
            return datetime.fromisoformat(json.loads(held["Body"].read())["started_at"])
        except (ValueError, KeyError, TypeError):
            return held["LastModified"]  # type: ignore[no-any-return]  # unreadable lock: age it by the object's own timestamp

    def release(self) -> None:
        """Delete the lock if it is still ours; never raises (the run's outcome matters more)."""
        try:
            held = json.loads(self.s3.get_object(Bucket=self.bucket, Key=self.key)["Body"].read())
            if held.get("run_id") == self.run_id:
                self.s3.delete_object(Bucket=self.bucket, Key=self.key)
        except Exception:
            logger.exception("could not release transform lock")


def prepare_extension_dir() -> str:
    """Copy the image's baked DuckDB extensions into writable /tmp once per container.

    The motherduck extension downloads its implementation at load time into the extension
    directory, which cannot be the read-only image. Every DuckDB connection (dbt profile, publish)
    reads HEADWAY_DUCKDB_EXTENSIONS, so it is pointed at the writable copy.
    """
    runtime = os.environ.get("HEADWAY_DUCKDB_EXTENSIONS_RUNTIME", RUNTIME_EXTENSION_DIR)
    baked = os.environ.get("HEADWAY_DUCKDB_EXTENSIONS_SRC")
    if baked and not os.path.isdir(runtime) and os.path.isdir(baked):
        tmp = f"{runtime}.{os.getpid()}.tmp"
        shutil.copytree(baked, tmp)
        os.rename(tmp, runtime)
    os.environ["HEADWAY_DUCKDB_EXTENSIONS"] = runtime
    return runtime


def assert_extension_dir_writable(runtime: str) -> bool:
    """Smoke check: a DuckDB connection configured with ``runtime`` can have extensions written
    into it (simulated by creating and renaming a file the way an extension install does)."""
    import duckdb

    con = duckdb.connect(config={"extension_directory": runtime, "home_directory": "/tmp"})  # noqa: S108
    try:
        configured = con.execute("select current_setting('extension_directory')").fetchone()
    finally:
        con.close()
    assert configured is not None and os.path.realpath(configured[0]) == os.path.realpath(runtime)
    target = os.path.join(runtime, "smoke_probe", "linux_arm64")
    os.makedirs(target, exist_ok=True)
    tmp = os.path.join(target, "probe.duckdb_extension.tmp")
    with open(tmp, "wb") as handle:
        handle.write(b"probe")
    os.rename(tmp, os.path.join(target, "probe.duckdb_extension"))
    shutil.rmtree(os.path.join(runtime, "smoke_probe"))
    return True


def _dbt_flags() -> list[str]:
    project = os.environ.get("DBT_PROJECT_DIR", "/var/task/dbt_project")
    return [
        "--project-dir", project,
        "--profiles-dir", project,
        "--target", os.environ.get("HEADWAY_DBT_TARGET", "lambda"),
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
    if args[0] == "run-operation" and not res.success:
        raise TransformError(f"dbt run-operation {args[1]} failed:\n{_dbt_log_tail()}")
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


def release_dbt_connections() -> None:
    """Close the DuckDB connection dbt-duckdb keeps open on the database file.

    dbt's connection outlives ``dbtRunner.invoke`` (the adapter caches it process-wide), and
    DuckDB refuses a second connection to the same file with a different configuration, so
    anything else that reads the file in this process must call this first. The next dbt
    invocation opens a fresh connection. A no-op when dbt is not installed (unit tests).
    """
    try:
        from dbt.adapters.duckdb.connections import DuckDBConnectionManager
    except ImportError:
        return
    with DuckDBConnectionManager._LOCK:  # noqa: SLF001
        env = DuckDBConnectionManager._ENV  # noqa: SLF001
        if env is not None:
            env.close()
        DuckDBConnectionManager.close_all_connections()


def _dbt_log_tail(lines: int = 25) -> str:
    """Last lines of the dbt log: where a failed run-operation reports its error."""
    try:
        with open(f"{DBT_LOG_PATH}/dbt.log") as handle:
            return "".join(handle.readlines()[-lines:]).strip()
    except OSError:
        return "(dbt log unavailable)"


def verify_export(s3: Any, bucket: str, gold_prefix: str) -> None:
    """Raise ExportError naming every expected gold object that is not in the bucket."""
    missing = []
    for key in [f"{gold_prefix}/{t}.parquet" for t in publish.gold_tables()] + [
        f"{gold_prefix}/manifest.json"
    ]:
        try:
            s3.head_object(Bucket=bucket, Key=key)
        except s3.exceptions.ClientError:
            missing.append(key)
    if missing:
        raise ExportError(
            f"export_gold left {len(missing)} object(s) missing from s3://{bucket}: "
            f"{', '.join(missing)}\nexport log:\n{_dbt_log_tail()}"
        )


GOLD_CACHE_CONTROL = "max-age=300"
_GOLD_CONTENT_TYPES = {".parquet": "application/vnd.apache.parquet", ".json": "application/json"}


def set_gold_cache_control(s3: Any, bucket: str, gold_prefix: str) -> list[str]:
    """Stamp ``Cache-Control: max-age=300`` on the exported gold Parquet files and manifest.

    DuckDB's COPY to S3 cannot set object headers, so each exported object is copied onto itself
    with replaced metadata. The live snapshot is written by ingest with its own, shorter header.
    """
    keys = [f"{gold_prefix}/{t}.parquet" for t in publish.gold_tables()] + [
        f"{gold_prefix}/manifest.json"
    ]
    for key in keys:
        s3.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": key},
            MetadataDirective="REPLACE",
            CacheControl=GOLD_CACHE_CONTROL,
            ContentType=_GOLD_CONTENT_TYPES[key[key.rindex(".") :]],
        )
    return keys


def r2_exclusions() -> list[str]:
    """Selectors keeping Release 2 models (tag r2) out unless HEADWAY_ENABLE_R2=true."""
    return [] if publish.r2_enabled() else ["tag:r2"]


def _build_and_test(variables: str, steps: dict[str, int] | None = None) -> DbtOutcome:
    """Load S3 data, build seeds and models, then run tests, each as its own timed step.

    The realtime staging models (tag ``s3_load``) are the only ones that read S3; building them
    first, once, means everything after reads the local database. `dbt build` skips every model
    downstream of a failing test, which silently dropped the gold aggregates, so models are built
    without tests and test failures are reported but never skip models.
    """

    def step(name: str, args: list[str]) -> DbtOutcome:
        t0 = time.monotonic()
        try:
            return run_dbt([*args, "--vars", variables])
        finally:
            ms = round((time.monotonic() - t0) * 1000)
            logger.info("step %s finished in %d ms", name, ms)
            if steps is not None:
                steps[name] = ms

    load = step("s3_load", ["run", "--select", "tag:s3_load"])
    if load.node_errors:
        raise TransformError(f"dbt S3 load failed: {', '.join(load.node_errors)}")
    build = step(
        "dbt_build", ["build", "--exclude", "resource_type:test", "tag:s3_load", *r2_exclusions()]
    )
    if build.node_errors:
        raise TransformError(f"dbt models failed: {', '.join(build.node_errors)}")
    tests = step(
        "dbt_test", ["test", *(["--exclude", *r2_exclusions()] if r2_exclusions() else [])]
    )
    tests.success = tests.success and build.success and load.success
    return tests


def _tables_to_publish(s3: Any, bucket: str, gold_prefix: str) -> list[str]:
    tables = list(publish.gold_tables())
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
    s3_gold = gold.startswith("s3://")
    if not s3_gold:
        os.makedirs(gold, exist_ok=True)
    variables = json.dumps(
        {
            "curated_root": root,
            "city_prefix": os.environ["CITY_ID"],
            "lookback_days": int(os.environ.get("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
        }
    )
    steps: dict[str, int] = {}
    build = _build_and_test(variables, steps)
    run_dbt(["run-operation", "export_gold", "--vars", variables,
             "--args", json.dumps({"target_dir": gold})])  # fmt: skip
    if s3_gold:
        bucket, _, prefix = gold.removeprefix("s3://").partition("/")
        client = boto3.client("s3")
        verify_export(client, bucket, prefix.rstrip("/"))
        body = client.get_object(Bucket=bucket, Key=f"{prefix.rstrip('/')}/manifest.json")["Body"]
        counts = json.loads(body.read())["row_counts"]
    else:
        with open(f"{gold}/manifest.json") as handle:
            counts = json.load(handle)["row_counts"]
    summary = {
        "smoke": True,
        "extension_dir_writable": assert_extension_dir_writable(
            os.environ["HEADWAY_DUCKDB_EXTENSIONS"]
        ),
        "dbt_status": "success" if build.success else "tests_failed",
        "r2_enabled": not r2_exclusions(),
        "tests_failed": build.tests_failed,
        "tests_passed": build.tests_passed,
        "rows_exported": sum(counts.values()),
        "tables_exported": len(counts),
        "step_ms": steps,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    logger.info(json.dumps(summary, sort_keys=True))
    return summary


def _record_run(s3: Any, run: RunRecord) -> None:
    """Append this run to the persisted agg_transform_runs; a failure here never fails the run."""
    try:
        finished = _now()
        state.append(
            s3, os.environ["RAW_BUCKET"], state.TRANSFORM_RUNS, [run.row(finished)], run.started_at
        )
    except Exception:
        logger.exception("could not write the run record")


def _variables(lookback: int, now: datetime) -> str:
    """The dbt vars of a run. Release 2 adds the history root and metric versions (ADR-015)."""
    variables: dict[str, Any] = {
        "curated_root": f"s3://{os.environ['RAW_BUCKET']}",
        "city_prefix": os.environ["CITY_ID"],
        "lookback_days": lookback,
    }
    if publish.r2_enabled():
        variables["history_root"] = (
            f"s3://{os.environ['PUBLIC_BUCKET']}/{history.history_prefix(os.environ['CITY_ID'])}"
        )
        variables["metric_versions"] = history.table_versions()
        # Curation runs every 15 minutes: the last half hour is incomplete, so the SLOs stop there.
        variables["slo_as_of"] = (now - SLO_LAG).strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(variables)


def _timezone() -> str:
    return os.environ.get("HEADWAY_TIMEZONE", DEFAULT_TIMEZONE)


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    started = time.monotonic()
    os.environ.setdefault("HOME", "/tmp")  # noqa: S108  Lambda leaves HOME unset
    prepare_extension_dir()
    if os.environ.get("HEADWAY_SMOKE_GOLD_DIR"):
        return _smoke(started)
    rebuild = None
    if event.get("task") == "rebuild_history":
        if not publish.r2_enabled():
            raise TransformError("rebuild_history needs HEADWAY_ENABLE_R2=true")
        rebuild = history.validate_rebuild(event)
    elif event.get("task"):
        raise TransformError(f"unknown task {event['task']!r}")
    city = os.environ["CITY_ID"]
    s3 = boto3.client("s3")
    drill = chaos.read(_now())
    lock = RunLock(s3, os.environ["RAW_BUCKET"], f"state/{city}/transform.lock")
    if not lock.acquire():
        # Another run (a scheduled run overlapping a manual one) holds the lock: not an error.
        summary: dict[str, Any] = {"skipped": "locked"}
        logger.info(json.dumps(summary, sort_keys=True))
        return summary
    run = RunRecord(lock.run_id, "rebuild_history" if rebuild else "scheduled", _now())
    db_path = os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108
    try:
        release_dbt_connections()  # a warm container may still hold the file
        state.load(s3, os.environ["RAW_BUCKET"], db_path, state.TRANSFORM_RUNS, run.started_at)
        if rebuild is not None:
            return _rebuild_history(started, s3, run, *rebuild, drill=drill)
        return _transform(started, s3, run, drill)
    except Exception as exc:
        run.error = f"{type(exc).__name__}: {exc}"[:1000]
        raise
    finally:
        _record_run(s3, run)
        lock.release()


def _transform(
    started: float, s3: Any, run: RunRecord, drill: chaos.Chaos = chaos.OFF
) -> dict[str, Any]:
    public_bucket = os.environ["PUBLIC_BUCKET"]
    city = os.environ["CITY_ID"]
    lookback = int(os.environ.get("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS))
    gold_prefix = f"public/{city}/gold"
    gold_url = f"s3://{public_bucket}/{gold_prefix}"
    db_path = os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108
    steps: dict[str, int] = run.steps
    summary: dict[str, Any] = {}

    def timed(name: str, t0: float) -> None:
        steps[name] = round((time.monotonic() - t0) * 1000)
        logger.info("step %s finished in %d ms", name, steps[name])

    variables = _variables(lookback, _now())
    build = _build_and_test(variables, steps)
    run.tests_passed, run.tests_failed = build.tests_passed, build.tests_failed
    if build.tests_failed:
        logger.warning("dbt tests failed: %d (export continues)", build.tests_failed)

    t0 = time.monotonic()
    run_dbt(["run-operation", "export_gold", "--vars", variables,
             "--args", json.dumps({"target_dir": gold_url})])  # fmt: skip
    verify_export(s3, public_bucket, gold_prefix)
    set_gold_cache_control(s3, public_bucket, gold_prefix)
    run.gold_exported_at = _now()
    timed("export_gold", t0)
    manifest = json.loads(
        s3.get_object(Bucket=public_bucket, Key=f"{gold_prefix}/manifest.json")["Body"].read()
    )
    rows_exported = manifest["row_counts"]
    run.rows_exported = sum(rows_exported.values())

    cost_rows: int | None = None
    t0 = time.monotonic()
    try:
        release_dbt_connections()  # cost reads dbt's database file; dbt's connection must go first
        cost_rows = cost.write_cost_daily(
            s3, public_bucket, gold_prefix, boto3.client("ce", region_name="us-east-1"),
            db_path, _now(),
        )  # fmt: skip
    except Exception as exc:
        logger.exception("cost report failed")
        summary["cost_error"] = f"{type(exc).__name__}: {exc}"
    timed("cost", t0)

    history_error: Exception | None = None
    if publish.r2_enabled():
        t0 = time.monotonic()
        try:
            release_dbt_connections()
            written = history.write_closed_days(
                s3, public_bucket, city, db_path, _now(),
                timezone=_timezone(), lookback_days=lookback,
            )  # fmt: skip
            summary["history_written"] = written.n_written
            summary["history_existing"] = written.existing
        except Exception as exc:
            logger.exception("history write failed")
            history_error = exc
            summary["history_error"] = f"{type(exc).__name__}: {exc}"
        timed("history", t0)

    publish_error: Exception | None = None
    t0 = time.monotonic()
    try:
        _fail_publish_in_drill(drill)
        token = boto3.client("ssm").get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)[
            "Parameter"
        ]["Value"]
        published = publish.publish_motherduck(
            token, gold_url, _tables_to_publish(s3, public_bucket, gold_prefix),
            **_history_publish_args(public_bucket, city),
        )  # fmt: skip
        summary["published_tables"] = len(published)
        run.gold_published = True
    except Exception as exc:
        logger.exception("MotherDuck publish failed")
        publish_error = exc
        summary["publish_error"] = f"{type(exc).__name__}: {exc}"
    timed("publish", t0)

    summary.update(
        {
            "gold_exported": True,
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
    if history_error is not None:
        raise history_error
    run.status = "success" if build.success else "tests_failed"
    return summary


def _fail_publish_in_drill(drill: chaos.Chaos) -> None:
    if drill.active("md_down"):
        raise ChaosError(f"chaos md_down until {drill.until}: MotherDuck publish refused")


def _history_publish_args(public_bucket: str, city: str) -> dict[str, Any]:
    if not publish.r2_enabled():
        return {}
    return {
        "history_url": f"s3://{public_bucket}/{history.history_prefix(city)}",
        "history_tables": list(history.HISTORY_TABLES),
    }


def _rebuild_history(
    started: float,
    s3: Any,
    run: RunRecord,
    first: date,
    last: date,
    version: int,
    drill: chaos.Chaos = chaos.OFF,
) -> dict[str, Any]:
    """Backfill history for explicit days at the current metric version; never touches gold.

    Builds the models over a lookback that reaches `first`, writes the days of the range that
    have no object yet for the tables whose family is at `version`, then refreshes the
    MotherDuck history tables. Existing days are left as they are: to change a definition, bump
    the constant in history.py and backfill that version.
    """
    public_bucket = os.environ["PUBLIC_BUCKET"]
    city = os.environ["CITY_ID"]
    db_path = os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108
    now = _now()
    lookback = max((now.date() - first).days, DEFAULT_LOOKBACK_DAYS)
    _build_and_test(_variables(lookback, now), run.steps)
    release_dbt_connections()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    result = history.write_closed_days(
        s3, public_bucket, city, db_path, now, timezone=_timezone(), lookback_days=lookback,
        days=days, tables=history.versions_for(version),
    )  # fmt: skip
    summary: dict[str, Any] = {
        "task": "rebuild_history",
        "metric_version": version,
        "history_written": result.n_written,
        "history_existing": result.existing,
        "written": result.written,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    if result.n_written:
        _fail_publish_in_drill(drill)
        token = boto3.client("ssm").get_parameter(Name=TOKEN_PARAMETER, WithDecryption=True)[
            "Parameter"
        ]["Value"]
        publish.publish_motherduck(
            token, "", [], **_history_publish_args(public_bucket, city)
        )  # fmt: skip
    logger.info(json.dumps(summary, sort_keys=True, default=str))
    run.status = "success"
    return summary
