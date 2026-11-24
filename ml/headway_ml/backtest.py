"""Backtest harness (`make backtest`): expanding-window weekly folds over fct_forecast_examples.

Every method is scored on exactly the same rows of a fold, in two views (see baselines.py):
strict (every method has a value) and full (documented fallback chain, share reported). Metrics
are per line x horizon x fold x period_type (normal vs alert_active, plus all), pooled over folds
with a cluster-bootstrap 95% interval and the spread of the per-fold MAE. A model comparison is
produced only for lines whose training gate is passed; the others get the reason and baselines.
With fewer than two weeks of data there is no fold: the baselines that need no training are
reported over the whole window, labelled descriptive (not a backtest).
"""

import argparse
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from headway_ml import baselines as bl
from headway_ml import data, folds, metrics, model, results, tracking

ROOT = Path(__file__).resolve().parents[2]
GROUP = ["line", "horizon_min", "period_type", "fold", "view", "method"]
COLUMNS = [
    *GROUP, "n", "n_labelled", "n_fallback", "fallback_share", "mae", "rmse", "within_60",
    "within_120", "ci_lo", "ci_hi", "fold_mae_min", "fold_mae_max", "fold_mae_std", "n_folds",
    "is_reference",
]  # fmt: skip


@dataclass
class Backtest:
    results: pd.DataFrame
    folds: list[folds.Fold]
    mode: str  # backtest | descriptive
    methods: list[str]
    notes: list[str] = field(default_factory=list)
    withheld: dict[str, str] = field(default_factory=dict)


def gate_status(gate: pd.DataFrame, lines: list[str]) -> tuple[set[str], dict[str, str]]:
    """Lines past the gate, and the reason for each line that is not."""
    by_line = gate.set_index("line")
    passed: set[str] = set()
    reasons: dict[str, str] = {}
    for line in lines:
        if line not in by_line.index:
            reasons[line] = "no gate row"
            continue
        g = by_line.loc[line]
        if bool(g["gate_passed"]):
            passed.add(line)
        else:
            reasons[line] = (
                f"gate not passed (n_days {g['n_days']} < {g['min_days']} or weekend days "
                f"{g['n_weekend_days']} < {g['min_weekend_days']})"
            )
    return passed, reasons


def _long(
    test: pd.DataFrame, raw: pd.DataFrame, methods: list[str], fold: str,
    model_pred: pd.Series | None,
) -> pd.DataFrame:  # fmt: skip
    """Signed errors of every method on the fold's rows, in both views."""
    filled = bl.filled_predictions(raw[methods], methods)
    full = raw[methods].notna().any(axis=1)
    strict = raw[methods].notna().all(axis=1)
    frames = []
    for view, mask in (("strict", strict), ("full", full)):
        cols = {m: (filled[m] if view == "full" else raw[m]) for m in methods}
        was_missing = {m: raw[m].isna() for m in methods}
        if model_pred is not None:
            cols[model.NAME] = model_pred
            was_missing[model.NAME] = pd.Series(False, index=test.index)
        for m, pred in cols.items():
            sel = mask & pred.notna()
            part = test.loc[sel, ["line", "horizon_min", "period_type", "prediction_ts"]].copy()
            part["err"] = (pred[sel] - test.loc[sel, "label_delay_s"]).to_numpy()
            part["fallback"] = was_missing[m][sel].to_numpy()
            part["fold"], part["view"], part["method"] = fold, view, m
            frames.append(part)
    return pd.concat(frames, ignore_index=True)


def _expand(df: pd.DataFrame, pool_folds: bool) -> pd.DataFrame:
    """Add the pooled period (all) and, for the pooled fold row, a copy labelled fold = all."""
    df = pd.concat([df, df.assign(period_type="all")], ignore_index=True)
    return pd.concat([df, df.assign(fold="all")], ignore_index=True) if pool_folds else df


def _aggregate(
    long: pd.DataFrame, labelled: pd.DataFrame, n_folds: int, reps: int, seed: int
) -> pd.DataFrame:  # fmt: skip
    pool = n_folds > 0
    long = _expand(long, pool)
    cell = ["line", "horizon_min", "period_type", "fold"]
    n_lab = _expand(labelled, pool).groupby(cell).size().rename("n_labelled")
    rows = []
    for key, g in long.groupby(GROUP, sort=True):
        s = metrics.summarize(g["err"].to_numpy(dtype="float64"))
        row: dict[str, object] = {**dict(zip(GROUP, key, strict=True)), **s}
        row["n"] = int(s["n"])
        row["n_fallback"] = int(g["fallback"].sum())
        row["fallback_share"] = row["n_fallback"] / row["n"]  # type: ignore[operator]
        row["ci_lo"] = row["ci_hi"] = np.nan
        if key[3] == "all" or not pool:
            clusters = pd.factorize(g["prediction_ts"])[0].astype("int64")
            row["ci_lo"], row["ci_hi"] = metrics.bootstrap_mae_ci(
                g["err"].to_numpy(dtype="float64"), clusters, reps, seed
            )
        rows.append(row)
    out = pd.DataFrame(rows)
    out = out.join(n_lab, on=cell)
    out["fold"] = out["fold"].astype(str)
    spread = (
        out[out["fold"] != "all"]
        .groupby(["line", "horizon_min", "period_type", "view", "method"])["mae"]
        .agg(fold_mae_min="min", fold_mae_max="max", fold_mae_std=lambda v: v.std(ddof=0))
    )
    out = out.join(spread, on=["line", "horizon_min", "period_type", "view", "method"])
    if pool:  # the spread belongs to the pooled rows
        out.loc[out["fold"] != "all", ["fold_mae_min", "fold_mae_max", "fold_mae_std"]] = np.nan
    else:
        out[["fold_mae_min", "fold_mae_max", "fold_mae_std"]] = np.nan
    out["n_folds"] = n_folds
    out["is_reference"] = out["method"].isin(bl.REFERENCES)
    return out[COLUMNS]


def run_backtest(
    examples: pd.DataFrame,
    gate: pd.DataFrame,
    *,
    min_train_weeks: int = 1,
    reps: int = 1000,
    seed: int = 7,
    forecaster: Callable[[], model.Forecaster] = model.LightGBMForecaster,
) -> Backtest:
    fl = folds.make_folds(examples["service_date"], min_train_weeks)
    lines = sorted(examples["line"].unique())
    passed, reasons = gate_status(gate, lines)
    notes: list[str] = []
    long_parts: list[pd.DataFrame] = []
    lab_parts: list[pd.DataFrame] = []
    keys = ["line", "horizon_min", "period_type"]
    if not fl:
        methods = [m for m in bl.METHODS if m != bl.DAYTYPE_HOUR]
        notes.append(
            "DESCRIPTIVE, NOT A BACKTEST: fewer than "
            f"{min_train_weeks + 1} weeks of data, so no fold exists. Baselines that need no "
            f"training are scored over the whole window; {bl.DAYTYPE_HOUR} needs training weeks "
            "and is not scored; no model is trained."
        )
        raw = bl.raw_predictions(examples, None)
        long_parts.append(_long(examples, raw, methods, "all", None))
        lab_parts.append(examples[keys].assign(fold="all"))
    else:
        methods = list(bl.METHODS)
        for f in fl:
            train, test = folds.split(examples, f)
            if test.empty or train.empty:
                continue
            raw = bl.raw_predictions(test, bl.fit_daytype_hour(train))
            model_pred = pd.Series(np.nan, index=test.index)
            for line in passed:
                tr, te = train[train["line"] == line], test[test["line"] == line]
                if tr.empty or te.empty:
                    continue
                fc = forecaster()
                fc.fit(tr)
                model_pred.loc[te.index] = fc.predict(te)
            long_parts.append(_long(test, raw, methods, str(f.index), model_pred))
            lab_parts.append(test[keys].assign(fold=str(f.index)))
    withheld = {line: why for line, why in reasons.items()}
    if withheld:
        for line, why in withheld.items():
            notes.append(f"model comparison withheld for {line}: {why}; baselines reported")
    if fl and not passed:
        notes.append("no line passed the training gate: no model was trained")
    long = pd.concat(long_parts, ignore_index=True)
    labelled = pd.concat(lab_parts, ignore_index=True)
    res = _aggregate(long, labelled, len(fl), reps, seed)
    return Backtest(res, fl, "backtest" if fl else "descriptive", methods, notes, withheld)


def report(bt: Backtest) -> str:
    out = [f"mode: {bt.mode}; folds: {len(bt.folds)}"]
    out += bt.notes
    pooled = bt.results[(bt.results["fold"] == "all") & (bt.results["period_type"] == "all")]
    for view in ("strict", "full"):
        v = pooled[pooled["view"] == view]
        if v.empty:
            continue
        mae = v.pivot_table(index=["line", "horizon_min"], columns="method", values="mae")
        n = v.groupby(["line", "horizon_min"])["n"].max().rename("n")
        out.append(f"\n{view} view, pooled MAE in seconds (mbta_reference is a reference):")
        table = mae.join(n).round(1)
        if view == "full":
            fb = v.pivot_table(
                index=["line", "horizon_min"], columns="method", values="fallback_share"
            )
            table = table.join((fb * 100).round(1).add_suffix(" fb%"))
        out.append(table.to_string())
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="headway_ml.backtest")
    parser.add_argument("--db", type=Path, default=ROOT / ".local" / "demo-r2" / "headway.duckdb")
    parser.add_argument("--out", type=Path, default=ROOT / ".local" / "backtest")
    parser.add_argument("--min-train-weeks", type=int, default=1)
    parser.add_argument("--bootstrap", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)
    if os.environ.get("HEADWAY_ENABLE_R3") != "true":
        print("backtest needs HEADWAY_ENABLE_R3=true (Release 3 flag)", file=sys.stderr)
        return 2
    examples = data.load_examples(args.db)
    gate = data.load_gate(args.db)
    if examples.empty:
        print("no labelled examples in", args.db, file=sys.stderr)
        return 1
    bt = run_backtest(
        examples, gate, min_train_weeks=args.min_train_weeks, reps=args.bootstrap, seed=args.seed
    )
    params = {
        "mode": bt.mode,
        "db": args.db.name,
        "data_window": f"{min(examples['service_date'])}..{max(examples['service_date'])}",
        "n_examples": len(examples),
        "folds": [f"{f.train_start}..{f.train_end}|{f.test_start}..{f.test_end}" for f in bt.folds],
        "horizons": sorted(examples["horizon_min"].unique().tolist()),
        "methods": bt.methods,
        "min_train_weeks": args.min_train_weeks,
        "bootstrap_reps": args.bootstrap,
        "seed": args.seed,
        "gate_passed_lines": sorted(set(examples["line"]) - set(bt.withheld)),
    }
    uri = tracking.tracking_uri(ROOT / ".local" / "mlflow")
    run_id = tracking.log_run(
        uri, params, {"git_sha": tracking.git_sha(ROOT), "mode": bt.mode}, data.FEATURES, bt.results
    )
    results.write_results(args.out / "backtest.duckdb", run_id, bt.results)
    print(report(bt))
    print(
        f"\nmlflow run {run_id} at {uri}; table agg_forecast_backtest in "
        f"{args.out / 'backtest.duckdb'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
