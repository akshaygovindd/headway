"""Forecasters that learn from the training weeks. Compared only for lines past the gate."""

from typing import Protocol

import numpy as np
import pandas as pd

from headway_ml.data import FEATURES

NAME = "lightgbm"
SEED = 7


class Forecaster(Protocol):
    def fit(self, train: pd.DataFrame) -> None: ...

    def predict(self, test: pd.DataFrame) -> np.ndarray: ...


def _matrix(df: pd.DataFrame) -> pd.DataFrame:
    x = df[[c for c in FEATURES if c != "line"]].copy()
    x["day_type"] = (x["day_type"] == "weekend").astype(int)
    x["active_disruption_alert"] = x["active_disruption_alert"].astype(int)
    return x.astype("float64")


class LightGBMForecaster:
    """Point forecast of the delay, one model per line (the gate is per line)."""

    def __init__(self) -> None:
        import lightgbm as lgb  # lazy: the library needs libomp on macOS; baselines do not

        self.model = lgb.LGBMRegressor(
            n_estimators=200, learning_rate=0.05, num_leaves=15, random_state=SEED, verbose=-1,
            deterministic=True, force_row_wise=True,
        )  # fmt: skip

    def fit(self, train: pd.DataFrame) -> None:
        self.model.fit(_matrix(train), train["label_delay_s"])

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return np.asarray(self.model.predict(_matrix(test)), dtype="float64")
