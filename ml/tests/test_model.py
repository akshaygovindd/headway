import numpy as np
import pandas as pd
import pytest
from headway_ml import backtest, baselines, folds, model
from synth import make_examples, make_gate

pytest.importorskip("lightgbm")


def _split() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = make_examples(weeks=4)
    fl = folds.make_folds(df["service_date"])[-1]
    return folds.split(df, fl)


def test_fit_predict_shape_and_categorical_line() -> None:
    train, test = _split()
    fc = model.LightGBMForecaster()
    fc.fit(train)
    pred = fc.predict(test)
    assert pred.shape == (len(test),) and np.isfinite(pred).all()
    assert fc.lines == ["Blue", "Red"]
    assert fc.best_params_["num_leaves"] in model.GRID["num_leaves"]
    assert fc.holdout_mae_ is not None


def test_deterministic_with_the_seed() -> None:
    train, test = _split()
    a, b = model.LightGBMForecaster(), model.LightGBMForecaster()
    a.fit(train)
    b.fit(train)
    np.testing.assert_array_equal(a.predict(test), b.predict(test))
    assert a.best_params_ == b.best_params_


def test_tuning_and_fit_never_see_the_test_week() -> None:
    train, test = _split()
    a = model.LightGBMForecaster()
    a.fit(train)
    shuffled = test["label_delay_s"].sample(frac=1, random_state=0).to_numpy()
    scrambled = test.assign(label_delay_s=shuffled)
    b = model.LightGBMForecaster()
    b.fit(train)  # the test frame is not an input of fit; predictions cannot use its labels
    np.testing.assert_array_equal(a.predict(scrambled), b.predict(test))
    inner, hold = model.split_holdout(train)
    assert inner["service_date"].max() < hold["service_date"].min()
    assert hold["service_date"].max() == train["service_date"].max()


def test_model_beats_baselines_on_a_seasonal_series() -> None:
    bt = backtest.run_backtest(make_examples(weeks=4), make_gate(passed=("Red", "Blue")), reps=20)
    pooled = bt.results[(bt.results["fold"] == "all") & (bt.results["period_type"] == "all")]
    pooled = pooled[pooled["view"] == "strict"]
    mae = pooled.groupby("method")["mae"].mean()
    assert mae[model.NAME] < mae[baselines.PERSISTENCE]
    assert mae[model.NAME] < mae[baselines.ROLLING]
