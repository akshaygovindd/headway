"""Feature parity of the served forecast with the training examples (ADR-019).

Usage: python scripts/serving_parity.py <built.duckdb> [--vars JSON] [--times N] [--assume-complete]

Runs inside the dbt environment, against a DuckDB file built with the Release 3 models. For a
sample of prediction times t it rebuilds the serving models on a copy with forecast_as_of = t and
requires, for every training example of t, a serving row of the same trip stop (line, t, horizon,
trip instance, stop sequence) whose feature columns are identical (rows whose run differs, a
reused trip id, are counted and capped at 5%). The serving models read the
same feature SQL as the examples (dbt/headway/macros/forecast_features.sql); this proves it on the
built data. The check fails when nothing was compared. --assume-complete is the same device as in
leakage_check.py: the sample's hours are incomplete windows, so every hour is marked complete on a
copy to give the examples rows to compare with.
"""

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

sys.path.insert(0, str(Path(__file__).parent))
import leakage_check as lc  # noqa: E402

KEY = ["line", "prediction_ts", "horizon_min", "trip_instance_key", "stop_sequence"]
SKIP = {"label_delay_s", "target_arrival_ts"}
# Share of examples allowed to be "ambiguous": a trip id reused by several runs of a day, where the
# run of the arrival that becomes the label (training) is not the run with the latest known arrival
# (serving), so the two legitimately read different upstream arrivals.
MAX_AMBIGUOUS = 0.05


def columns(db: Path) -> list[str]:
    con = duckdb.connect(str(db), read_only=True)
    cols = [
        r[0]
        for r in con.execute(
            "select column_name from (describe int_forecast_serving_features)"
        ).fetchall()
    ]
    con.close()
    return [c for c in cols if c not in SKIP]


def rows(
    db: Path, table: str, cols: list[str], t: datetime
) -> dict[tuple[Any, ...], tuple[Any, ...]]:
    con = duckdb.connect(str(db), read_only=True)
    con.execute("set TimeZone = 'UTC'")
    got = con.execute(
        f"select {', '.join(cols)} from {table} where prediction_ts = ?",
        [t],  # noqa: S608
    ).fetchall()
    con.close()
    idx = [cols.index(k) for k in KEY]
    return {tuple(r[i] for i in idx): r for r in got}


def parity(db: Path, run_vars: str, count: int) -> int:
    times = lc.pick_times(db, count)
    if not times:
        print("serving parity: no forecast examples to compare", file=sys.stderr)
        return 1
    compared = ambiguous = 0
    failed = False
    with tempfile.TemporaryDirectory(prefix="headway-parity-") as tmp:
        for t in times:
            copy = Path(tmp) / "serve" / db.name
            copy.parent.mkdir(exist_ok=True)
            shutil.copyfile(db, copy)
            vars_ = {**json.loads(run_vars), "forecast_as_of": t.isoformat()}
            lc.dbt_run(copy, json.dumps(vars_), "int_forecast_serving_grid+")
            cols = columns(copy)
            train = rows(db, "fct_forecast_examples", cols, t)
            serve = rows(copy, "int_forecast_serving_features", cols, t)
            for key, row in train.items():
                other = serve.get(key)
                if other is None:
                    failed = True
                    print(f"PARITY t={t}: example {key} has no serving row", file=sys.stderr)
                    continue
                seg = cols.index("segment")
                if other[seg] is not None and row[seg] != other[seg]:
                    ambiguous += 1
                    continue
                compared += 1
                diff = [
                    (c, a, b)
                    for c, a, b in zip(cols, row, other, strict=True)
                    if a != b and c != "segment"
                ]
                if diff:
                    failed = True
                    print(f"PARITY t={t}: {key} differs: {diff[:5]}", file=sys.stderr)
    if failed:
        return 1
    if ambiguous > MAX_AMBIGUOUS * (compared + ambiguous):
        print(
            f"serving parity: {ambiguous} of {compared + ambiguous} ambiguous runs", file=sys.stderr
        )
        return 1
    if compared == 0:
        print("serving parity proved nothing: no examples compared", file=sys.stderr)
        return 1
    print(
        f"serving parity passed: {len(times)} prediction times, {compared} trip stops compared, "
        f"{ambiguous} skipped (reused trip id, run differs)"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("--vars", default="{}")
    parser.add_argument("--times", type=int, default=3)
    parser.add_argument("--assume-complete", action="store_true")
    args = parser.parse_args(argv)
    if not args.assume_complete:
        return parity(args.db, args.vars, args.times)
    with tempfile.TemporaryDirectory(prefix="headway-parity-base-") as tmp:
        return parity(lc.assume_complete(args.db, args.vars, Path(tmp)), args.vars, args.times)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
