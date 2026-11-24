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


class _Spy:
    """Records the frames it is fitted and asked about; predicts the training mean."""

    fits: list[pd.DataFrame] = []
    preds: list[pd.DataFrame] = []

    def fit(self, train: pd.DataFrame) -> None:
        _Spy.fits.append(train)
        self.mean = float(train["label_delay_s"].mean())

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        _Spy.preds.append(test)
        return np.full(len(test), self.mean)


def test_only_training_folds_are_sampled_scoring_uses_the_full_test_week() -> None:
    df = make_examples()
    _Spy.fits, _Spy.preds = [], []
    capped = backtest.run_backtest(
        df, make_gate(passed=("Red", "Blue")), reps=20, forecaster=_Spy, train_cap_per_day=40
    )
    full = backtest.run_backtest(df, make_gate(passed=("Red", "Blue")), reps=20, forecaster=_Spy)
    assert _Spy.fits
    assert all(f.groupby("service_date").size().max() <= 40 + 6 for f in _Spy.fits[:2])
    fl = folds.make_folds(df["service_date"])
    for f, test in zip(fl, _Spy.preds[:2], strict=True):
        assert len(test) == len(folds.split(df, f)[1])  # the whole test week, unsampled
    for view in ("strict", "full"):
        a = capped.results[(capped.results["fold"] == "all") & (capped.results["view"] == view)]
        b = full.results[(full.results["fold"] == "all") & (full.results["view"] == view)]
        for m in (baselines.PERSISTENCE, baselines.ROLLING, baselines.MBTA, "lightgbm"):
            na = a[(a["method"] == m)].set_index(["line", "horizon_min", "period_type"])["n"]
            nb = b[(b["method"] == m)].set_index(["line", "horizon_min", "period_type"])["n"]
            pd.testing.assert_series_equal(na, nb)  # identical examples scored
    # every baseline and the reference are scored identically with or without the cap
    for m in (baselines.PERSISTENCE, baselines.DAYTYPE_HOUR, baselines.ROLLING, baselines.MBTA):
        p = capped.results[(capped.results["method"] == m)]["mae"].to_numpy()
        q = full.results[(full.results["method"] == m)]["mae"].to_numpy()
        assert np.allclose(p, q)


def _noisy(sd: float) -> pd.DataFrame:
    df = make_examples(weeks=4)
    rng = np.random.default_rng(3)
    df["label_delay_s"] = df["label_delay_s"] + rng.normal(0, sd, len(df))
    return df


class _Oracle:
    """Predicts the label (it cannot be beaten by a baseline): a clear win."""

    def fit(self, train: pd.DataFrame) -> None: ...

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return test["label_delay_s"].to_numpy() + 1.0


class _Wrong:
    """Always 800 s late: a clear loss."""

    def fit(self, train: pd.DataFrame) -> None: ...

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return np.full(len(test), 800.0)


class _CopiesBestBaseline:
    """Predicts the daytype_hour table's value (the best baseline on this data): a tie."""

    def fit(self, train: pd.DataFrame) -> None:
        self.table = baselines.fit_daytype_hour(train)

    def predict(self, test: pd.DataFrame) -> np.ndarray:
        return baselines.raw_predictions(test, self.table)[baselines.DAYTYPE_HOUR].to_numpy()


def _paired(forecaster: object, line: str = "Red", h: int = 15) -> pd.Series:
    bt = backtest.run_backtest(
        _noisy(150.0), make_gate(passed=("Red", "Blue")), reps=200,
        forecaster=forecaster,  # type: ignore[arg-type]
    )  # fmt: skip
    return pooled(bt.results, "strict", "lightgbm", line, h)


def test_paired_comparison_model_clearly_wins() -> None:
    r = _paired(_Oracle)
    assert r["best_baseline"] == baselines.DAYTYPE_HOUR
    assert r["diff_mae"] < -50 and r["diff_ci_hi"] < 0  # CI entirely below 0: model better


def test_paired_comparison_model_clearly_loses() -> None:
    r = _paired(_Wrong)
    assert r["diff_mae"] > 100 and r["diff_ci_lo"] > 0  # CI entirely above 0: model worse


def test_paired_comparison_tie_has_an_interval_spanning_zero() -> None:
    r = _paired(_CopiesBestBaseline)
    assert r["diff_mae"] == pytest.approx(0.0, abs=1e-9)
    assert r["diff_ci_lo"] <= 0 <= r["diff_ci_hi"]


def test_paired_interval_is_tighter_than_two_separate_ones() -> None:
    """Resampling the same clusters for both methods removes the shared noise."""
    r = _paired(_CopiesBestBaseline)
    assert (r["ci_hi"] - r["ci_lo"]) > 0
    assert (r["diff_ci_hi"] - r["diff_ci_lo"]) < (r["ci_hi"] - r["ci_lo"])


def test_paired_columns_only_on_pooled_strict_model_rows() -> None:
    bt = backtest.run_backtest(
        _noisy(150.0), make_gate(passed=("Red", "Blue")), reps=50, forecaster=_Oracle
    )  # fmt: skip
    has = bt.results[bt.results["diff_mae"].notna()]
    assert set(has["method"]) == {"lightgbm"} and set(has["view"]) == {"strict"}
    assert set(has["fold"]) == {"all"}
    full_model = bt.results[(bt.results["method"] == "lightgbm") & (bt.results["view"] == "full")]
    assert full_model["diff_mae"].isna().all()
