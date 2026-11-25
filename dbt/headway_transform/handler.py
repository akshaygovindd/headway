"""AWS Lambda entry point: dbt build, gold export, MotherDuck publish, daily cost report."""

import json
import logging
import os
import shutil
import tempfile
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3

from headway_transform import (
    chaos,
    cost,
    duckdb_limits,
    emf,
    forecast_serving,
    forecast_state,
    history,
    incident_summaries,
    memory,
    observability,
    publish,
    state,
)

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

    con = duckdb_limits.connect(config={"extension_directory": runtime, "home_directory": "/tmp"})  # noqa: S108
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


def _exists(s3: Any, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
    except s3.exceptions.ClientError:
        return False
    return True


def drop_stale_r3_tables(
    s3: Any, bucket: str, gold_prefix: str, exported: dict[str, Any]
) -> list[str]:
    """The backtest is exported only while some line is past the gate: an object left from an
    earlier run is removed when this export did not write it, so gold never keeps a result the
    current gate would not allow."""
    removed = []
    for name in publish.R3_OPTIONAL_TABLES:
        key = f"{gold_prefix}/{name}.parquet"
        if name not in exported and _exists(s3, bucket, key):
            s3.delete_object(Bucket=bucket, Key=key)
            removed.append(key)
    return removed


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
    for name in publish.R3_OPTIONAL_TABLES:
        if _exists(s3, bucket, f"{gold_prefix}/{name}.parquet"):
            keys.append(f"{gold_prefix}/{name}.parquet")
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
    """Selectors keeping Release 2 models (tag r2) out, and Release 3 (tag r3) unless enabled.

    Release 2 selectors apply unless HEADWAY_ENABLE_R2=true; Release 3 models (internal, built on
    the Release 2 ones) need HEADWAY_ENABLE_R3=true and Release 2 on.
    """
    out = [] if publish.r2_enabled() else ["tag:r2"]
    if not (publish.r3_enabled() and publish.r2_enabled()):
        out.append("tag:r3")
    return out


_TRACKER: memory.Tracker | None = None


def _mark_memory(step: str) -> None:
    """Close a step's memory and spill accounting (no-op outside a transform run)."""
    if _TRACKER is not None:
        _TRACKER.mark(step)


def _stop_memory_tracking() -> None:
    global _TRACKER
    if _TRACKER is not None:
        _TRACKER.stop()
        _TRACKER = None


def _build_and_test(
    variables: str,
    steps: dict[str, int] | None = None,
    between: Callable[[], None] | None = None,
) -> DbtOutcome:
    """Load S3 data, build seeds and models, then run tests, each as its own timed step.

    The realtime staging models (tag ``s3_load``) are the only ones that read S3; building them
    first, once, means everything after reads the local database. `dbt build` skips every model
    downstream of a failing test, which silently dropped the gold aggregates, so models are built
    without tests and test failures are reported but never skip models. ``between`` runs after the
    models exist and before the tests (Elementary's history is restored there).
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
            _mark_memory(name)

    load = step("s3_load", ["run", "--select", "tag:s3_load"])
    if load.node_errors:
        raise TransformError(f"dbt S3 load failed: {', '.join(load.node_errors)}")
    build = step(
        "dbt_build", ["build", "--exclude", "resource_type:test", "tag:s3_load", *r2_exclusions()]
    )
    if build.node_errors:
        raise TransformError(f"dbt models failed: {', '.join(build.node_errors)}")
    if between is not None:
        between()
    tests = step(
        "dbt_test", ["test", *(["--exclude", *r2_exclusions()] if r2_exclusions() else [])]
    )
    tests.success = tests.success and build.success and load.success
    return tests


def _analyst_examples(s3: Any, bucket: str, gold_prefix: str) -> dict[str, Any]:
    """Precompute the analyst's example answers from the exported gold (no LLM) and publish them
    as gold/analyst_examples.json. Never fails the run."""
    import tempfile

    from headway_mcp import examples
    from headway_mcp.tools import Tools

    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(f"{tmp}/gold")
            for name in [*examples.required_tables(), "manifest"]:
                ext = "json" if name == "manifest" else "parquet"
                try:
                    s3.download_file(
                        bucket, f"{gold_prefix}/{name}.{ext}", f"{tmp}/gold/{name}.{ext}"
                    )
                except s3.exceptions.ClientError:
                    continue  # a table this export does not have: its examples are left out
            path = f"{tmp}/gold/analyst_examples.json"
            n = examples.write(Tools(f"file://{tmp}"), path)
            with open(path, "rb") as handle:
                s3.put_object(
                    Bucket=bucket,
                    Key=f"{gold_prefix}/analyst_examples.json",
                    Body=handle.read(),
                    ContentType="application/json",
                    CacheControl=GOLD_CACHE_CONTROL,
                )
        return {"analyst_examples": n}
    except Exception as exc:
        logger.exception("analyst examples failed")
        return {"analyst_examples_error": f"{type(exc).__name__}: {exc}"}


def _tables_to_publish(s3: Any, bucket: str, gold_prefix: str) -> list[str]:
    tables = list(publish.gold_tables())
    for name in publish.OPTIONAL_TABLES:
        try:
            s3.head_object(Bucket=bucket, Key=f"{gold_prefix}/{name}.parquet")
        except s3.exceptions.ClientError:
            continue
        tables.append(name)
    return tables


def _smoke_r2(
    variables: dict[str, Any], s3: Any, public_bucket: str, db_path: str
) -> dict[str, Any]:
    """Release 2 smoke steps on the S3 target, in the order a run takes them: run-state append and
    load, the SLO models built over that state, history write (and that it is immutable), and the
    SLO models again, now reading the history they just wrote."""

    raw_bucket = os.environ.get("RAW_BUCKET", "smoke-raw")
    city = os.environ["CITY_ID"]
    now = datetime(2026, 12, 1, tzinfo=UTC)  # after every sample service day has closed
    lookback = int(variables["lookback_days"])
    variables_json = json.dumps(variables)

    def slo() -> dict[str, Any]:
        outcome = run_dbt(
            ["build", "--select", "stg_transform_runs", "agg_slo_daily", "agg_slo_rollup",
             "--exclude", "resource_type:test", "resource_type:unit_test",
             "--vars", variables_json]
        )  # fmt: skip
        if outcome.node_errors:
            raise TransformError(f"smoke SLO rebuild failed: {', '.join(outcome.node_errors)}")
        release_dbt_connections()
        con = duckdb_limits.connect(db_path, read_only=True)
        try:
            daily = con.execute("select count(*) from agg_slo_daily").fetchone()
            rollup = con.execute("select count(*) from agg_slo_rollup").fetchone()
            events = con.execute(
                "select sum(good_events), sum(total_events) from agg_slo_daily "
                "where slo_name = 'transform_success'"
            ).fetchone()
        finally:
            con.close()
        assert daily and rollup and events
        return {
            "daily": int(daily[0]),
            "rollup": int(rollup[0]),
            "events": [int(events[0] or 0), int(events[1] or 0)],
        }

    release_dbt_connections()
    started = datetime(2026, 11, 22, 14, 20, tzinfo=UTC)
    run = RunRecord(
        "smoke-run", "scheduled", started, status="success", tests_passed=1, tests_failed=0,
        rows_exported=1, gold_exported_at=started, gold_published=True,
    )  # fmt: skip
    finished = started + timedelta(minutes=2)
    state.append(s3, raw_bucket, state.TRANSFORM_RUNS, [run.row(finished)], started)
    loaded = state.load(s3, raw_bucket, db_path, state.TRANSFORM_RUNS, now)
    built = slo()

    first = history.write_closed_days(
        s3, public_bucket, city, db_path, now, timezone=_timezone(), lookback_days=lookback
    )
    prefix = history.history_prefix(city)
    keys = [
        history.history_key(prefix, table, history.version_of(table), date.fromisoformat(day))
        for table, days in first.written.items()
        for day in days
    ]
    etags = {k: s3.head_object(Bucket=public_bucket, Key=k)["ETag"] for k in keys}
    second = history.write_closed_days(
        s3, public_bucket, city, db_path, now, timezone=_timezone(), lookback_days=lookback
    )
    unchanged = all(
        s3.head_object(Bucket=public_bucket, Key=k)["ETag"] == v for k, v in etags.items()
    )
    from_history = slo()
    return {
        "state_rows_loaded": loaded,
        "slo_daily_rows": built["daily"],
        "slo_rollup_rows": built["rollup"],
        "transform_success_events": built["events"],
        "history_written": first.n_written,
        "history_existing_on_rewrite": second.existing,
        "history_rewritten": second.n_written,
        "history_objects_unchanged": unchanged,
        "slo_rows_read_back_from_history": from_history["daily"] == built["daily"]
        and from_history["events"] == built["events"],
    }


def _smoke_forecast_state(s3: Any, raw_bucket: str, db_path: str, lookback: int) -> dict[str, Any]:
    """Release 3: the closed-day forecast tables written once to the raw bucket (and not again)."""
    release_dbt_connections()
    now = datetime(2026, 12, 1, tzinfo=UTC)  # after every sample service day has closed
    first = forecast_state.write_closed_days(
        s3, raw_bucket, db_path, now, timezone=_timezone(), lookback_days=None
    )
    second = forecast_state.write_closed_days(
        s3, raw_bucket, db_path, now, timezone=_timezone(), lookback_days=None
    )
    return {
        "forecast_state_written": first.n_written,
        "forecast_state_tables": sorted(first.written),
        "forecast_state_rewritten": second.n_written,
        "forecast_state_existing_on_rewrite": second.existing,
    }


def _smoke_observability(
    s3: Any, public_bucket: str, raw_bucket: str, db_path: str, city: str, started_at: datetime
) -> dict[str, Any]:
    """Observability steps on the S3 target, after the build: the run's metric lines (printed, so
    the smoke script can look for them in the container log), Elementary's rows persisted and read
    back into a fresh database, and the report rendered by `edr` and uploaded."""

    release_dbt_connections()
    now = _now()
    sample_now = datetime(2026, 11, 22, 16, 20, tzinfo=UTC)  # just after the sample's last hour
    metrics = emf.collect(
        db_path, sample_now, previous=sample_now - timedelta(minutes=60), exported_at=sample_now,
        r2=publish.r2_enabled(), lag=SLO_LAG, timezone=_timezone(),
    )  # fmt: skip
    emf.emit(sample_now, metrics)
    persisted = observability.persist(s3, raw_bucket, db_path, started_at, now)
    with tempfile.TemporaryDirectory() as tmp:
        fresh = os.path.join(tmp, "fresh.duckdb")
        duckdb_limits.connect(fresh).close()
        restored = observability.restore(s3, raw_bucket, fresh, now)
    key = observability.publish_report(s3, public_bucket, city, db_path, now)
    report = s3.get_object(Bucket=public_bucket, Key=key)
    body = report["Body"].read()
    return {
        "emf_metrics": sorted(metrics),
        "elementary_persisted": persisted,
        "elementary_restored": restored,
        "report_key": key,
        "report_bytes": len(body),
        "report_is_html": b"window.elementaryData" in body[:200] and b"<html" in body,
        "report_content_type": report["ContentType"],
    }


def _smoke_analyst_examples(s3: Any, bucket: str, gold_prefix: str) -> dict[str, Any]:
    """The analyst-examples export step as the run takes it, then the object read back: it must
    exist, be JSON and hold as many examples as the step reported."""
    out = _analyst_examples(s3, bucket, gold_prefix)
    if "analyst_examples_error" in out:
        return out
    body = s3.get_object(Bucket=bucket, Key=f"{gold_prefix}/analyst_examples.json")["Body"].read()
    doc = json.loads(body)
    items = doc.get("examples", doc) if isinstance(doc, dict) else doc
    out["analyst_examples_read_back"] = len(items)
    return out


def _smoke_db_path() -> str:
    return os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108


def _smoke(started: float) -> dict[str, Any]:
    """Image smoke test: dbt build + gold export on a local curated root; no AWS, no publish.

    With Release 2 on and an S3 gold target it also runs the history, run-state and SLO steps."""
    global _TRACKER
    _TRACKER = tracker = memory.Tracker(duckdb_limits.settings()["temp_directory"]).start()
    run_started = _now()
    root = os.environ["HEADWAY_SMOKE_CURATED_ROOT"]
    gold = os.environ["HEADWAY_SMOKE_GOLD_DIR"]
    s3_gold = gold.startswith("s3://")
    if not s3_gold:
        os.makedirs(gold, exist_ok=True)
    variables: dict[str, Any] = {
        "curated_root": root,
        "city_prefix": os.environ["CITY_ID"],
        "lookback_days": int(os.environ.get("LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS)),
    }
    r2_s3 = s3_gold and publish.r2_enabled()
    if r2_s3:
        bucket_name = gold.removeprefix("s3://").partition("/")[0]
        variables["history_root"] = (
            f"s3://{bucket_name}/{history.history_prefix(os.environ['CITY_ID'])}"
        )
        variables["metric_versions"] = history.table_versions()
    variables_json = json.dumps(variables)
    steps: dict[str, int] = {}
    r3_s3 = r2_s3 and publish.r3_enabled()
    forecast: dict[str, Any] = {}
    if r3_s3:
        forecast.update(_forecast_reports(boto3.client("s3"), _smoke_db_path()))
    build = _build_and_test(variables_json, steps)
    if r3_s3:
        forecast.update(_forecast_serving(boto3.client("s3"), _smoke_db_path()))
    if r3_s3:
        forecast.update(_incident_summaries(boto3.client("s3"), _smoke_db_path(), smoke=True))
    run_dbt(["run-operation", "export_gold", "--vars", variables_json,
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
        "r2_enabled": publish.r2_enabled(),
        "tests_failed": build.tests_failed,
        "tests_passed": build.tests_passed,
        "rows_exported": sum(counts.values()),
        "tables_exported": len(counts),
        "r2_tables_exported": len(set(counts) & set(publish.R2_GOLD_TABLES)),
        "r3_tables_exported": len(
            set(counts) & {*publish.R3_GOLD_TABLES, *publish.R3_OPTIONAL_TABLES}
        ),
        "r3_backtest_exported": bool(set(counts) & set(publish.R3_OPTIONAL_TABLES)),
        "step_ms": steps,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    summary.update(forecast)
    if r3_s3:
        summary.update(_smoke_analyst_examples(client, bucket, prefix.rstrip("/")))
        cache = Path(os.environ.get("HEADWAY_FORECAST_MODEL_DIR", "/tmp/forecast_model"))  # noqa: S108
        summary["forecast_cache_files"] = sorted(p.name for p in cache.rglob("*") if p.is_file())
    if r2_s3:
        db = _smoke_db_path()
        summary.update(_smoke_r2(variables, client, bucket, db))
        if publish.r3_enabled():
            summary.update(
                _smoke_forecast_state(
                    client, os.environ.get("RAW_BUCKET", "smoke-raw"), db,
                    int(variables["lookback_days"]),
                )
            )  # fmt: skip
        summary.update(
            _smoke_observability(
                client, bucket, os.environ.get("RAW_BUCKET", "smoke-raw"), db,
                os.environ["CITY_ID"], run_started,
            )
        )  # fmt: skip
    tracker.stop()
    _TRACKER = None
    summary.update(tracker.report())
    emf.emit_memory(_now(), float(summary["memory_used_pct"]))
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


def _with_forecast_as_of(variables: str, now: datetime) -> str:
    """The serving prediction time follows the clock: forecast_as_of is now (Release 3)."""
    parsed = json.loads(variables)
    parsed["forecast_as_of"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    return json.dumps(parsed)


def _forecast_reports(s3: Any, db_path: str) -> dict[str, Any]:
    """Load the training job's reports before the build; a failure leaves the tables empty."""
    try:
        release_dbt_connections()
        return {"forecast_reports": forecast_serving.load_reports(
            s3, os.environ["RAW_BUCKET"], db_path
        )}  # fmt: skip
    except Exception as exc:
        logger.exception("could not load the forecast reports")
        return {"forecast_reports_error": f"{type(exc).__name__}: {exc}"}


def _incident_summaries(s3: Any, db_path: str, *, smoke: bool = False) -> dict[str, Any]:
    """Write the incident summaries (LLM with the number check, else the template); never fails
    the run. In the image smoke test only: HEADWAY_SMOKE_SEED_INCIDENTS seeds two incidents and
    HEADWAY_SMOKE_FAKE_LLM replaces the provider with a fake whose first answer must be rejected."""
    try:
        release_dbt_connections()
        out: dict[str, Any] = {}
        client: incident_summaries.LlmClient | None
        if smoke and os.environ.get("HEADWAY_SMOKE_SEED_INCIDENTS"):
            out["incident_seeded"] = incident_summaries.seed_smoke_incidents(db_path)
        if smoke and os.environ.get("HEADWAY_SMOKE_FAKE_LLM"):
            client = incident_summaries.SmokeLlm()
        else:
            try:
                client = incident_summaries.build_client(boto3.client("ssm"))
            except Exception:  # no SSM client (region, endpoint): the template is the fallback
                logger.exception("could not read the incident LLM key")
                client = None
        out.update(
            incident_summaries.summarize(
                s3, os.environ["RAW_BUCKET"], db_path, _now(), client=client
            )
        )
        return out
    except Exception as exc:
        logger.exception("incident summaries failed")
        return {
            "incident_summary_status": "failed",
            "incident_summary_error": f"{type(exc).__name__}: {exc}",
        }


def _forecast_serving(s3: Any, db_path: str) -> dict[str, Any]:
    """Score the next hour with the registered model; never fails the run (baselines only)."""
    try:
        release_dbt_connections()
        return forecast_serving.serve(s3, os.environ["RAW_BUCKET"], db_path)
    except Exception as exc:
        logger.exception("forecast serving failed")
        return {
            "forecast_serving_status": "failed",
            "forecast_serving_error": f"{type(exc).__name__}: {exc}",
        }


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
    elif event.get("task") == "rebuild_forecast_examples":
        if not (publish.r2_enabled() and publish.r3_enabled()):
            raise TransformError("rebuild_forecast_examples needs HEADWAY_ENABLE_R2 and R3")
        rebuild = forecast_state.validate_rebuild(event)
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
    task = event.get("task") or "scheduled"
    run = RunRecord(lock.run_id, task, _now())
    db_path = os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108
    try:
        release_dbt_connections()  # a warm container may still hold the file
        state.load(s3, os.environ["RAW_BUCKET"], db_path, state.TRANSFORM_RUNS, run.started_at)
        if rebuild is not None and task == "rebuild_forecast_examples":
            return _rebuild_forecast_examples(started, s3, run, *rebuild)
        if rebuild is not None:
            return _rebuild_history(started, s3, run, *rebuild, drill=drill)
        try:
            return _transform(started, s3, run, drill)
        finally:
            _stop_memory_tracking()
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

    global _TRACKER
    _TRACKER = tracker = memory.Tracker(duckdb_limits.settings()["temp_directory"]).start()

    def timed(name: str, t0: float) -> None:
        steps[name] = round((time.monotonic() - t0) * 1000)
        logger.info("step %s finished in %d ms", name, steps[name])
        _mark_memory(name)

    try:
        previous_export = emf.previous_export(db_path)
    except Exception:
        logger.exception("could not read the previous export time")
        previous_export = None
    variables = _variables(lookback, _now())
    if publish.r2_enabled() and publish.r3_enabled():
        variables = _with_forecast_as_of(variables, _now())
        t0 = time.monotonic()
        summary.update(_forecast_reports(s3, db_path))
        timed("forecast_reports", t0)
    build = _build_and_test(variables, steps, lambda: _restore_observability(s3, db_path))
    run.tests_passed, run.tests_failed = build.tests_passed, build.tests_failed
    if build.tests_failed:
        logger.warning("dbt tests failed: %d (export continues)", build.tests_failed)

    if publish.r2_enabled() and publish.r3_enabled():
        t0 = time.monotonic()
        summary.update(_forecast_serving(s3, db_path))
        timed("forecast_serving", t0)
        t0 = time.monotonic()
        summary.update(_incident_summaries(s3, db_path))
        timed("incident_summaries", t0)

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
    if publish.r2_enabled() and publish.r3_enabled():
        drop_stale_r3_tables(s3, public_bucket, gold_prefix, rows_exported)
    run.rows_exported = sum(rows_exported.values())

    if publish.r2_enabled() and publish.r3_enabled():
        t0 = time.monotonic()
        summary.update(_analyst_examples(s3, public_bucket, gold_prefix))
        timed("analyst_examples", t0)

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

    _emit_metrics(db_path, previous_export, run.gold_exported_at)

    t0 = time.monotonic()
    summary.update(_publish_observability(s3, public_bucket, city, db_path, run.started_at))
    timed("observability", t0)

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

    forecast_error: Exception | None = None
    if publish.r2_enabled() and publish.r3_enabled():
        t0 = time.monotonic()
        try:
            release_dbt_connections()
            fwritten = forecast_state.write_closed_days(
                s3, os.environ["RAW_BUCKET"], db_path, _now(),
                timezone=_timezone(), lookback_days=lookback,
            )  # fmt: skip
            summary["forecast_state_written"] = fwritten.n_written
            summary["forecast_state_existing"] = fwritten.existing
        except Exception as exc:
            logger.exception("forecast state write failed")
            forecast_error = exc
            summary["forecast_state_error"] = f"{type(exc).__name__}: {exc}"
        timed("forecast_state", t0)

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

    tracker.stop()
    _TRACKER = None
    summary.update(tracker.report())
    emf.emit_memory(_now(), summary["memory_used_pct"])
    if summary["memory_used_pct"] >= memory.ALARM_PCT:
        logger.warning(
            "transform peak memory %d MB is %.1f%% of its %d MB limit",
            summary["peak_memory_mb"], summary["memory_used_pct"], summary["memory_limit_mb"],
        )  # fmt: skip
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
    if forecast_error is not None:
        raise forecast_error
    run.status = "success" if build.success else "tests_failed"
    return summary


def _restore_observability(s3: Any, db_path: str) -> None:
    """Restore Elementary's history before the tests that read it; never fails the run."""
    try:
        release_dbt_connections()
        restored = observability.restore(s3, os.environ["RAW_BUCKET"], db_path, _now())
        logger.info("elementary history restored: %s", json.dumps(restored, sort_keys=True))
    except Exception:
        logger.exception("could not restore the Elementary history")


def _publish_observability(
    s3: Any, public_bucket: str, city: str, db_path: str, started_at: datetime
) -> dict[str, Any]:
    """Persist this run's Elementary rows and, once a day, publish the report. Never raises."""
    out: dict[str, Any] = {}
    now = _now()
    try:
        release_dbt_connections()
        out["elementary_persisted"] = observability.persist(
            s3, os.environ["RAW_BUCKET"], db_path, started_at, now
        )
    except Exception as exc:
        logger.exception("could not persist the Elementary history")
        out["observability_error"] = f"{type(exc).__name__}: {exc}"
        return out
    try:
        if observability.report_due(s3, public_bucket, city, now):
            out["observability_report"] = observability.publish_report(
                s3, public_bucket, city, db_path, now
            )
    except Exception as exc:
        logger.exception("could not publish the observability report")
        out["observability_error"] = f"{type(exc).__name__}: {exc}"
    return out


def _emit_metrics(db_path: str, previous: datetime | None, exported_at: datetime | None) -> None:
    """Print the run's EMF metric lines; never fails the run. dbt's connection is already closed."""
    now = _now()
    try:
        metrics = emf.collect(
            db_path, now, previous=previous, exported_at=exported_at or now,
            r2=publish.r2_enabled(), lag=SLO_LAG, timezone=_timezone(),
        )  # fmt: skip
        emf.emit(now, metrics)
    except Exception:
        logger.exception("could not emit run metrics")


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


def _rebuild_forecast_examples(
    started: float, s3: Any, run: RunRecord, first: date, last: date, version: int
) -> dict[str, Any]:
    """Backfill the closed-day forecast tables for explicit days at `version`; never touches gold.

    Builds the models over a lookback that reaches `first`, then writes the days of the range
    that have no object yet. Existing days are left as they are."""
    now = _now()
    lookback = max((now.date() - first).days, DEFAULT_LOOKBACK_DAYS)
    _build_and_test(_variables(lookback, now), run.steps)
    release_dbt_connections()
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    db_path = os.environ.get("HEADWAY_DUCKDB_PATH", "/tmp/headway.duckdb")  # noqa: S108
    result = forecast_state.write_closed_days(
        s3, os.environ["RAW_BUCKET"], db_path, now, timezone=_timezone(),
        lookback_days=lookback, days=days, only_version=version,
    )  # fmt: skip
    summary: dict[str, Any] = {
        "task": "rebuild_forecast_examples",
        "metric_version": version,
        "written": result.written,
        "existing": result.existing,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    logger.info(json.dumps(summary, sort_keys=True, default=str))
    run.status = "success"
    return summary
