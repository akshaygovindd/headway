"""AWS Lambda entry point of the weekly forecast training (ADR-019).

Reads the closed-day examples and gate days the transform keeps in the raw bucket, computes the
gate, runs the backtest and (for lines past the gate only) trains one model, registers it under
models/forecast/ and logs the run to MLflow. The MLflow database is downloaded from S3 at the
start, used in /tmp and uploaded at the end; a lock object keeps it to one writer.
"""

import json
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger()
logger.setLevel(logging.INFO)

WORKDIR = Path(os.environ.get("HEADWAY_TRAIN_WORKDIR", "/tmp/train"))  # noqa: S108
DEFAULT_WINDOW_DAYS = 56  # eight weekly folds at most
DEFAULT_MAX_ROWS_PER_DAY = 8000
MLFLOW_PREFIX = "mlflow"


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    started = time.monotonic()
    os.environ.setdefault("HOME", "/tmp")  # noqa: S108  Lambda leaves HOME unset
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    import boto3

    from headway_ml import s3store

    bucket = os.environ["RAW_BUCKET"]
    s3 = boto3.client("s3")
    # A fresh directory per invocation: MLflow caches its SQLite engine by path, and a warm
    # container must not keep a handle on the previous run's (replaced) database file.
    run_dir = WORKDIR / uuid.uuid4().hex
    try:
        with s3store.mlflow_db(s3, bucket, run_dir / "mlflow") as db:
            summary = _train(s3, bucket, db, run_dir / "state", started)
    except s3store.Locked:
        summary = {"skipped": "locked"}
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    logger.info(json.dumps(summary, sort_keys=True, default=str))
    return summary


def _train(s3: Any, bucket: str, db: Path, state_dir: Path, started: float) -> dict[str, Any]:
    import mlflow

    from headway_ml import data, registry, sampling, state_data, tracking, train

    window = int(os.environ.get("TRAIN_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))
    cap = int(os.environ.get("TRAIN_MAX_ROWS_PER_DAY", DEFAULT_MAX_ROWS_PER_DAY))
    examples, gate_days = state_data.load(s3, bucket, state_dir, window)
    if examples.empty or gate_days.empty:
        return {"status": "no_data", "examples": len(examples), "gate_days": len(gate_days)}
    gate = data.gate_from_days(gate_days)
    sha = os.environ.get("HEADWAY_GIT_SHA") or "unknown"
    result = train.run_training(
        examples, gate, git_sha=sha, reps=int(os.environ.get("BOOTSTRAP_REPS", "300")),
        train_cap_per_day=cap,
    )  # fmt: skip
    version: int | None = None
    if result.artifact is not None:
        version = registry.write_model(s3, bucket, result.artifact, gate)
    uri = f"sqlite:///{db}"
    run_id = tracking.log_run(
        uri,
        {
            "mode": result.backtest.mode,
            "data_window": f"{min(examples['service_date'])}..{max(examples['service_date'])}",
            "n_examples": len(examples),
            "methods": result.backtest.methods,
            "gate_passed_lines": result.passed,
            "model_version": version if version is not None else "none",
            "model_params": result.artifact.params if result.artifact else "none",
            "train_cap_per_day": cap,
            "sample_seed": 7,
            **{f"gate_reason.{ln}": why for ln, why in result.reasons.items()},
        },
        {"git_sha": sha, "model_written": str(version is not None).lower()},
        data.FEATURES,
        result.backtest.results,
        artifacts=f"s3://{bucket}/{MLFLOW_PREFIX}/artifacts",
        files={
            "gate_reasons.json": json.dumps(result.reasons, indent=2),
            "notes.json": json.dumps(result.notes, indent=2),
            "sampling.json": result.sampled.to_json(orient="records", indent=2),
        },
        extra_metrics=tracking.sampling_metrics(result.sampled),
    )
    mlflow.set_tracking_uri(uri)
    runs = mlflow.search_runs(experiment_names=[tracking.EXPERIMENT])
    listing = s3.list_objects_v2(Bucket=bucket, Prefix=f"{registry.PREFIX}/")
    return {
        "status": "trained" if version is not None else "baselines_only",
        "run_id": run_id,
        "mlflow_runs": len(runs),
        "model_version": version,
        "gate_passed_lines": result.passed,
        "gate_reasons": result.reasons,
        "notes": result.notes,
        "model_objects": sorted(o["Key"] for o in listing.get("Contents", [])),
        "examples": len(examples),
        "train_rows_by_line": sampling.rows_by_line(result.sampled),
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
