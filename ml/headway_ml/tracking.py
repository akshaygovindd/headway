"""MLflow logging on a SQLite backend store (mlflow.db) with a separate artifact root. Locally
.local/mlflow/; in the training Lambda the db lives in /tmp and is synced with S3 (s3store.py)."""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import mlflow
import pandas as pd

EXPERIMENT = "headway-forecast-backtest"


def git_sha(root: Path) -> str:
    try:
        done = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True
        )
    except OSError:
        return "unknown"
    return done.stdout.strip() if done.returncode == 0 and done.stdout.strip() else "unknown"


def tracking_uri(default_dir: Path) -> str:
    """HEADWAY_MLFLOW_URI, else the SQLite database default_dir/mlflow.db (.local/mlflow)."""
    env = os.environ.get("HEADWAY_MLFLOW_URI")
    if env:
        return env
    default_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(default_dir / 'mlflow.db').resolve()}"


def artifact_root(default_dir: Path) -> str:
    """HEADWAY_MLFLOW_ARTIFACT_ROOT (s3://<raw>/mlflow/artifacts), else default_dir/artifacts."""
    return os.environ.get("HEADWAY_MLFLOW_ARTIFACT_ROOT") or (
        (default_dir / "artifacts").resolve().as_uri()
    )


def metric_name(*parts: object) -> str:
    return re.sub(r"[^A-Za-z0-9_\-. /]", "_", ".".join(str(p) for p in parts))


def log_run(
    uri: str,
    params: dict[str, Any],
    tags: dict[str, str],
    features: list[str],
    results: pd.DataFrame,
    *,
    artifacts: str | None = None,
    files: dict[str, str] | None = None,
    extra_metrics: dict[str, float] | None = None,
) -> str:
    """One run: params, tags (git sha, feature list), a feature artifact and the pooled
    per line x horizon metrics of every method and view. Returns the run id."""
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    mlflow.set_tracking_uri(uri)
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=artifacts)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run() as run:
        mlflow.log_params({k: str(v)[:500] for k, v in params.items()})
        mlflow.set_tags(tags)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "features.json"
            path.write_text(json.dumps(features, indent=2))
            mlflow.log_artifact(str(path))
            for name, text in (files or {}).items():  # e.g. gate_reasons.json, metrics.json
                extra = Path(tmp) / name
                extra.write_text(text)
                mlflow.log_artifact(str(extra))
        pooled = results[(results["fold"] == "all") & (results["period_type"] == "all")]
        metrics: dict[str, float] = {}
        for r in pooled.itertuples():
            for key in ("mae", "rmse", "within_60", "within_120", "fallback_share"):
                value = getattr(r, key)
                if pd.notna(value):
                    metrics[metric_name(r.view, r.method, r.line, f"h{r.horizon_min}", key)] = (
                        float(value)
                    )
        metrics.update(extra_metrics or {})
        mlflow.log_metrics(metrics)
        return str(run.info.run_id)


def sampling_metrics(stats: pd.DataFrame) -> dict[str, float]:
    """Training rows per line (and per line x horizon) before and after the stratified cap."""
    out: dict[str, float] = {}
    for line, h, before, after in zip(
        stats["line"], stats["horizon_min"], stats["n_before"], stats["n_after"], strict=True
    ):
        out[metric_name("train_rows", line, f"h{h}")] = float(int(after))
        out[metric_name("train_rows_before", line, f"h{h}")] = float(int(before))
    for line, g in stats.groupby("line"):
        out[metric_name("train_rows", line)] = float(g["n_after"].sum())
        out[metric_name("train_rows_before", line)] = float(g["n_before"].sum())
    return out
