from datetime import date, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from headway_ml import backtest, baselines, folds, metrics, results, tracking
from synth import START, make_examples, make_gate


def pooled(res: pd.DataFrame, view: str, method: str, line: str = "Red", h: int = 15) -> pd.Series:
    sel = (
        (res["fold"] == "all")
        & (res["period_type"] == "all")
        & (res["view"] == view)
        & (res["method"] == method)
        & (res["line"] == line)
        & (res["horizon_min"] == h)
    )
    assert sel.sum() == 1
    return res[sel].iloc[0]


def test_persistence_is_exact_when_the_series_is_persistent() -> None:
    bt = backtest.run_backtest(make_examples(persistence_exact=True), make_gate(), reps=50)
    p = pooled(bt.results, "full", baselines.PERSISTENCE)
    assert p["mae"] == pytest.approx(0.0)
    assert p["within_60"] == 100.0
    for other in (baselines.DAYTYPE_HOUR, baselines.ROLLING):
        assert pooled(bt.results, "full", other)["mae"] > 50


def test_daytype_hour_wins_on_a_seasonal_series() -> None:
    bt = backtest.run_backtest(make_examples(), make_gate(), reps=50)
    best = pooled(bt.results, "strict", baselines.DAYTYPE_HOUR)
    assert best["mae"] == pytest.approx(0.0)
    for other in (baselines.PERSISTENCE, baselines.ROLLING):
        assert pooled(bt.results, "strict", other)["mae"] > 100
    assert bt.mode == "backtest"
    assert len(bt.folds) == 2


def test_folds_never_overlap_and_the_test_week_follows_training() -> None:
    dates = pd.Series([START + timedelta(days=d) for d in range(5 * 7 + 3)])
    fl = folds.make_folds(dates)
    assert [f.index for f in fl] == [1, 2, 3, 4, 5]
    for a, b in zip(fl, fl[1:], strict=False):
        assert a.test_end < b.test_start
    for f in fl:
        assert f.train_start == START and f.train_end < f.test_start
        assert (f.test_end - f.test_start).days <= 6
    assert fl[-1].test_end >= max(dates)  # the partial last week is tested, not dropped
    with pytest.raises(ValueError):
        folds.check_folds([fl[0], fl[0]])
    bad = folds.Fold(1, START, START + timedelta(days=9), START + timedelta(days=7), START)
    with pytest.raises(ValueError):
        folds.check_folds([bad])


def test_folds_split_rows_by_date_only() -> None:
    df = make_examples()
    for f in folds.make_folds(df["service_date"]):
        train, test = folds.split(df, f)
        assert train["service_date"].max() < test["service_date"].min()
        assert len(train) + len(test) <= len(df)


def test_daytype_hour_never_sees_test_labels() -> None:
    df = make_examples()
    fl = folds.make_folds(df["service_date"])
    clean = backtest.run_backtest(df, make_gate(), reps=20).results
    poisoned = df.copy()
    last_test = poisoned["service_date"] >= fl[-1].test_start
    poisoned.loc[last_test, "label_delay_s"] += 1e6
    # table fitted from the training weeks only is identical whatever the test labels are
    train_a, _ = folds.split(df, fl[-1])
    train_b, _ = folds.split(poisoned, fl[-1])
    pd.testing.assert_frame_equal(
        baselines.fit_daytype_hour(train_a), baselines.fit_daytype_hour(train_b)
    )
    assert train_b["service_date"].max() < fl[-1].test_start
    # and the poisoned last fold scores the baseline with the huge error, not a cheated zero
    res = backtest.run_backtest(poisoned, make_gate(), reps=20).results
    f_last = res[(res["fold"] == str(fl[-1].index)) & (res["method"] == baselines.DAYTYPE_HOUR)]
    assert (f_last["mae"] > 9e5).all()
    f_first = clean[
        (clean["fold"] == str(fl[0].index)) & (clean["method"] == baselines.DAYTYPE_HOUR)
    ]
    assert (f_first["mae"] < 1e-6).all()


def test_fit_daytype_hour_signature_takes_no_test_frame() -> None:
    assert baselines.fit_daytype_hour.__code__.co_varnames[:1] == ("train",)
    assert baselines.fit_daytype_hour.__code__.co_argcount == 1


def test_strict_and_full_views_and_fallback_share() -> None:
    df = make_examples(weeks=2, lines=("Red",), horizons=(15,))
    n = len(df)
    df.loc[df.index[: n // 4], "last_upstream_delay_s"] = np.nan  # persistence missing: 25 %
    df.loc[df.index[n - 10 :], "rolling_median_delay_60m"] = np.nan
    both = (df.index < n // 4) & (df.index >= n - 10)
    assert not both.any()
    bt = backtest.run_backtest(df, make_gate(("Red",)), reps=20)
    test = df[df["service_date"] >= START + timedelta(days=7)]
    miss = test["last_upstream_delay_s"].isna().mean()
    full = pooled(bt.results, "full", baselines.PERSISTENCE)
    strict = pooled(bt.results, "strict", baselines.PERSISTENCE)
    assert full["fallback_share"] == pytest.approx(miss)
    assert full["n"] == len(test)
    assert (
        strict["n"]
        == test[["last_upstream_delay_s", "rolling_median_delay_60m"]].notna().all(axis=1).sum()
    )
    assert strict["n"] < full["n"]
    assert pooled(bt.results, "strict", baselines.ROLLING)["n"] == strict["n"]  # same examples
    assert pooled(bt.results, "strict", baselines.PERSISTENCE)["fallback_share"] == 0.0


def test_fallback_chain_is_persistence_then_rolling_median() -> None:
    raw = pd.DataFrame(
        {
            baselines.PERSISTENCE: [np.nan, 5.0],
            baselines.DAYTYPE_HOUR: [9.0, 9.0],
            baselines.ROLLING: [7.0, 7.0],
            baselines.MBTA: [np.nan, np.nan],
        }
    )
    f = baselines.filled_predictions(raw, baselines.METHODS)
    assert f[baselines.PERSISTENCE].tolist() == [7.0, 5.0]
    assert f[baselines.MBTA].iloc[0] == 7.0 and f[baselines.MBTA].iloc[1] == 5.0


def test_period_types_and_pooling_and_ci() -> None:
    bt = backtest.run_backtest(make_examples(), make_gate(), reps=200)
    r = bt.results
    assert set(r["period_type"]) == {"normal", "alert_active", "all"}
    p = pooled(r, "full", baselines.PERSISTENCE)
    assert p["ci_lo"] <= p["mae"] <= p["ci_hi"]
    assert p["fold_mae_min"] <= p["mae"] <= p["fold_mae_max"]
    assert p["n_folds"] == 2
    ref = r[r["method"] == baselines.MBTA]
    assert ref["is_reference"].all() and not r[r["method"] != baselines.MBTA]["is_reference"].any()
    again = backtest.run_backtest(make_examples(), make_gate(), reps=200).results
    pd.testing.assert_frame_equal(bt.results, again)  # seeded: identical


def test_bootstrap_interval_shrinks_with_data() -> None:
    rng = np.random.default_rng(0)
    err = rng.normal(0, 100, 4000)
    wide = metrics.bootstrap_mae_ci(err[:100], np.arange(100), 500, 1)
    narrow = metrics.bootstrap_mae_ci(err, np.arange(4000), 500, 1)
    assert narrow[1] - narrow[0] < wide[1] - wide[0]


class _Mean:
    def fit(self, train: pd.DataFrame) -> None:
        self.m = float(train["label_delay_s"].mean())

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return np.full(len(test), self.m)


def test_model_comparison_only_for_lines_past_the_gate(capsys: pytest.CaptureFixture[str]) -> None:
    df = make_examples()
    bt = backtest.run_backtest(df, make_gate(passed=("Red",)), reps=20, forecaster=_Mean)
    models = bt.results[bt.results["method"] == "lightgbm"]
    assert set(models["line"]) == {"Red"}
    assert "Blue" in bt.withheld and "gate not passed" in bt.withheld["Blue"]
    assert any("model comparison withheld for Blue" in n for n in bt.notes)
    blue = bt.results[bt.results["line"] == "Blue"]
    assert set(blue["method"]) == set(baselines.METHODS)  # baselines still reported
    # the model is scored on the same rows as the baselines
    assert (
        pooled(bt.results, "full", "lightgbm")["n"]
        == pooled(bt.results, "full", baselines.PERSISTENCE)["n"]
    )
    none = backtest.run_backtest(df, make_gate(), reps=20, forecaster=_Mean)
    assert "lightgbm" not in set(none.results["method"])
    assert any("no line passed the training gate" in n for n in none.notes)


def test_less_than_two_weeks_is_descriptive_without_daytype_hour() -> None:
    bt = backtest.run_backtest(make_examples(weeks=1), make_gate(), reps=20)
    assert bt.mode == "descriptive" and bt.folds == []
    assert baselines.DAYTYPE_HOUR not in set(bt.results["method"])
    assert any("DESCRIPTIVE, NOT A BACKTEST" in n for n in bt.notes)


def test_results_table_and_mlflow_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADWAY_MLFLOW_URI", raising=False)
    bt = backtest.run_backtest(make_examples(), make_gate(), reps=20)
    uri = tracking.tracking_uri(tmp_path / "mlflow")
    run_id = tracking.log_run(
        uri,
        {"data_window": "2026-11-02..2026-11-22", "folds": [1, 2]},
        {"git_sha": "abc1234"},
        ["a", "b"],
        bt.results,
    )
    import mlflow

    run = mlflow.get_run(run_id)
    assert run.data.tags["git_sha"] == "abc1234"
    assert run.data.params["data_window"] == "2026-11-02..2026-11-22"
    assert any(k.startswith("full.persistence.Red.h15.mae") for k in run.data.metrics)
    results.write_results(tmp_path / "bt.duckdb", run_id, bt.results)
    results.write_results(tmp_path / "bt.duckdb", run_id, bt.results)  # rerun replaces
    con = duckdb.connect(str(tmp_path / "bt.duckdb"))
    count = con.execute("select count(*) from agg_forecast_backtest").fetchone()
    assert count is not None and count[0] == len(bt.results)


def test_cli_refuses_without_the_r3_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADWAY_ENABLE_R3", raising=False)
    assert backtest.main(["--db", "missing.duckdb"]) == 2


def test_gate_status_reasons() -> None:
    passed, why = backtest.gate_status(make_gate(passed=("Red",)), ["Red", "Blue", "Green"])
    assert passed == {"Red"}
    assert "gate not passed" in why["Blue"] and why["Green"] == "no gate row"


def test_fold_ranges_use_dates() -> None:
    assert folds.make_folds(pd.Series([date(2026, 11, 2)])) == []
    assert folds.make_folds(pd.Series([], dtype=object)) == []
