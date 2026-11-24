"""MLflow logging: a file store by default, any tracking URI (and artifact root) by environment."""

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
    """HEADWAY_MLFLOW_URI, else the file store at default_dir (.local/mlflow)."""
    return os.environ.get("HEADWAY_MLFLOW_URI") or default_dir.resolve().as_uri()


def metric_name(*parts: object) -> str:
    return re.sub(r"[^A-Za-z0-9_\-. /]", "_", ".".join(str(p) for p in parts))


def log_run(
    uri: str,
    params: dict[str, Any],
    tags: dict[str, str],
    features: list[str],
    results: pd.DataFrame,
) -> str:
    """One run: params, tags (git sha, feature list), a feature artifact and the pooled
    per line x horizon metrics of every method and view. Returns the run id."""
    # MLflow 3.18 calls the file store maintenance mode and refuses it unless opted in; a file
    # store is what the plan asks for (no server). Move to the S3 path in 3.2.
    os.environ.setdefault("MLFLOW_ALLOW_FILE_STORE", "true")
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    mlflow.set_tracking_uri(uri)
    artifact_root = os.environ.get("HEADWAY_MLFLOW_ARTIFACT_ROOT")
    if mlflow.get_experiment_by_name(EXPERIMENT) is None:
        mlflow.create_experiment(EXPERIMENT, artifact_location=artifact_root)
    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run() as run:
        mlflow.log_params({k: str(v)[:500] for k, v in params.items()})
        mlflow.set_tags(tags)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "features.json"
            path.write_text(json.dumps(features, indent=2))
            mlflow.log_artifact(str(path))
        pooled = results[(results["fold"] == "all") & (results["period_type"] == "all")]
        metrics: dict[str, float] = {}
        for r in pooled.itertuples():
            for key in ("mae", "rmse", "within_60", "within_120", "fallback_share"):
                value = getattr(r, key)
                if pd.notna(value):
                    metrics[metric_name(r.view, r.method, r.line, f"h{r.horizon_min}", key)] = (
                        float(value)
                    )
        mlflow.log_metrics(metrics)
        return str(run.info.run_id)
