"""Forecaster that learns from the training weeks: one LightGBM model across the lines past the
gate, `line` a categorical feature. Compared only for those lines."""

import itertools
from typing import Any, Protocol

import numpy as np
import pandas as pd

from headway_ml.data import FEATURES

NAME = "lightgbm"
SEED = 7
GRID = {"num_leaves": [7, 15], "min_child_samples": [20, 50]}
N_TREES = 200
HOLDOUT_SHARE = 0.2  # of the training service days, the latest ones; never the test week


class Forecaster(Protocol):
    def fit(self, train: pd.DataFrame) -> None: ...

    def predict(self, test: pd.DataFrame) -> np.ndarray: ...


def feature_names() -> list[str]:
    return list(FEATURES)


def matrix(df: pd.DataFrame, lines: list[str]) -> pd.DataFrame:
    x = df[feature_names()].copy()
    x["line"] = pd.Categorical(x["line"], categories=lines)
    x["day_type"] = (x["day_type"] == "weekend").astype(int)
    x["active_disruption_alert"] = x["active_disruption_alert"].astype(int)
    for c in x.columns:
        if c != "line":
            x[c] = x[c].astype("float64")
    return x


def _regressor(params: dict[str, Any]) -> Any:
    import lightgbm as lgb  # lazy: the library needs libomp on macOS; baselines do not

    return lgb.LGBMRegressor(
        n_estimators=N_TREES, learning_rate=0.05, random_state=SEED, verbose=-1,
        deterministic=True, force_row_wise=True, n_jobs=1, **params,
    )  # fmt: skip


def split_holdout(train: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split of the training frame: the last HOLDOUT_SHARE of its service days."""
    days = sorted(train["service_date"].unique())
    n_hold = max(1, round(len(days) * HOLDOUT_SHARE))
    cut = days[-n_hold]
    return train[train["service_date"] < cut], train[train["service_date"] >= cut]


class LightGBMForecaster:
    """Point forecast of the delay. The hyperparameters are picked on a holdout cut from the
    end of the training frame (so the test week never takes part), then the model is refitted
    on the whole training frame."""

    def __init__(self) -> None:
        self.model: Any = None
        self.lines: list[str] = []
        self.best_params_: dict[str, Any] = {}
        self.holdout_mae_: float | None = None

    def fit(self, train: pd.DataFrame) -> None:
        self.lines = sorted(train["line"].unique())
        x_all, y_all = matrix(train, self.lines), train["label_delay_s"]
        inner, hold = split_holdout(train)
        best: tuple[float, dict[str, Any]] | None = None
        if len(inner) and len(hold):
            xi, yi = matrix(inner, self.lines), inner["label_delay_s"]
            xh, yh = matrix(hold, self.lines), hold["label_delay_s"].to_numpy()
            for values in itertools.product(*GRID.values()):
                params = dict(zip(GRID, values, strict=True))
                m = _regressor(params)
                m.fit(xi, yi)
                mae = float(np.mean(np.abs(m.predict(xh) - yh)))
                if best is None or mae < best[0]:
                    best = (mae, params)
        mae, params = best if best else (float("nan"), {k: v[0] for k, v in GRID.items()})
        self.best_params_, self.holdout_mae_ = params, (None if best is None else mae)
        self.model = _regressor(params)
        self.model.fit(x_all, y_all)

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict(matrix(test, self.lines)), dtype="float64")

    def save(self, path: str) -> None:
        self.model.booster_.save_model(path)
