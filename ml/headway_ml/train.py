"""Weekly training: backtest, then one model over the lines past the gate, registered in S3.

The gate is enforced here and again in registry.write_model. Lines that did not pass are never
trained on; they are reported with baselines and the reason.
"""

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from headway_ml import backtest, model, registry


@dataclass
class TrainResult:
    backtest: backtest.Backtest
    passed: list[str]
    reasons: dict[str, str]
    artifact: registry.ModelArtifact | None = None
    notes: list[str] = field(default_factory=list)


def pooled_metrics(res: pd.DataFrame) -> list[dict[str, Any]]:
    pooled = res[(res["fold"] == "all") & (res["period_type"] == "all")]
    cols = [
        "line",
        "horizon_min",
        "view",
        "method",
        "n",
        "mae",
        "rmse",
        "within_60",
        "ci_lo",
        "ci_hi",
    ]
    clean = pooled[cols].astype(object).where(pooled[cols].notna(), None)
    return [{str(k): v for k, v in r.items()} for r in clean.to_dict("records")]


def run_training(
    examples: pd.DataFrame,
    gate: pd.DataFrame,
    *,
    git_sha: str,
    reps: int = 1000,
    seed: int = 7,
    min_train_weeks: int = 1,
) -> TrainResult:
    bt = backtest.run_backtest(
        examples, gate, min_train_weeks=min_train_weeks, reps=reps, seed=seed
    )
    passed, reasons = backtest.gate_status(gate, sorted(examples["line"].unique()))
    result = TrainResult(bt, sorted(passed), reasons, notes=list(bt.notes))
    if not passed:
        result.notes.append("no line passed the training gate: baselines only, no model written")
        return result
    rows = examples[examples["line"].isin(passed)]
    fc = model.LightGBMForecaster()
    fc.fit(rows)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model.txt"
        fc.save(str(path))
        text = path.read_text()
    result.artifact = registry.ModelArtifact(
        model_text=text,
        lines=fc.lines,
        features=model.feature_names(),
        window={
            "train_start": str(rows["service_date"].min()),
            "train_end": str(rows["service_date"].max()),
            "n_examples": len(rows),
            "backtest_mode": bt.mode,
            "n_folds": len(bt.folds),
        },
        metrics=pooled_metrics(bt.results),
        git_sha=git_sha,
        params={**fc.best_params_, "holdout_mae": fc.holdout_mae_, "seed": model.SEED},
    )
    return result
