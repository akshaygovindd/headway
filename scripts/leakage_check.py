"""Leakage check C1 for the forecast examples (ADR-019).

Usage: python scripts/leakage_check.py <built.duckdb> [--vars JSON] [--times N] [--assume-complete]

Runs inside the dbt environment, against a DuckDB file built with the Release 3 models. For a
sample of prediction times t it copies the database, deletes from every input of the features the
rows that are not known at t (known_at, collected_at or fetched_at after t, or an event time after
t), rebuilds int_forecast_line_features and fct_forecast_examples on the copy with dbt, and requires
every feature column of the examples of t to be identical to the full build. The targets
(int_forecast_targets) are kept: they are the rows to forecast, and the label is the one column
that is allowed to come from after t. The check fails when nothing was compared or nothing was
deleted, because then it would prove nothing.

--assume-complete is for the sample of the e2e test, whose two hours are incomplete windows and so
all outage hours (no examples). On a copy, it marks every hour as complete and rebuilds the
examples, so the check has rows to compare. The outage filter only selects rows; it computes no
feature, so this does not weaken the check.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DBT = ["uv", "run", "--project", "dbt", "dbt"]
DBT_ARGS = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local"]
LABEL_COLUMNS = {"label_delay_s", "target_arrival_ts"}
KEY = "line, direction_id, stop_id, prediction_ts, horizon_min, trip_instance_key, segment"
# input table -> predicates selecting the rows that are not known at t ({t} is the cut)
CUTS = {
    "int_usable_arrivals": "known_at > {t} or arrival_ts > {t}",
    "fct_headways": "known_at > {t} or to_arrival_ts > {t}",
    "fct_stop_predictions": "collected_at > {t}",
    "int_forecast_alert_captures": "fetched_at > {t}",
}


def pick_times(db: Path, count: int) -> list[datetime]:
    """Prediction times spread over the examples, preferring those with many features set."""
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute(
        "select prediction_ts from fct_forecast_examples group by 1 having count(*) > 0 order by 1"
    ).fetchall()
    con.close()
    times = [r[0] for r in rows]
    if len(times) <= count:
        return times
    step = (len(times) - 1) / (count - 1) if count > 1 else 0
    return [times[round(i * step)] for i in range(count)]


def features(db: Path, t: datetime) -> tuple[list[str], list[tuple[Any, ...]]]:
    con = duckdb.connect(str(db), read_only=True)
    cols = [
        r[0]
        for r in con.execute("select column_name from (describe fct_forecast_examples)").fetchall()
        if r[0] not in LABEL_COLUMNS
    ]
    rows = con.execute(
        f"select {', '.join(cols)} from fct_forecast_examples "  # noqa: S608
        f"where prediction_ts = ? order by {KEY}",
        [t],
    ).fetchall()
    con.close()
    return cols, rows


def dbt_run(db: Path, run_vars: str, *select: str) -> None:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    env["HEADWAY_DUCKDB_PATH"] = str(db)
    done = subprocess.run(
        [*DBT, "run", *DBT_ARGS, "--vars", run_vars, "--select", *select],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )  # fmt: skip
    if done.returncode != 0:
        print(done.stdout[-3000:], file=sys.stderr)
        raise SystemExit(f"dbt run {' '.join(select)} failed")


def assume_complete(db: Path, run_vars: str, tmp: Path) -> Path:
    """A copy of db with no outage hours and the examples rebuilt from it."""
    full = tmp / "full" / db.name  # the file name is the catalog name stored in the database
    full.parent.mkdir()
    shutil.copyfile(db, full)
    con = duckdb.connect(str(full))
    con.execute("update int_hour_flags set data_outage = false")
    con.execute("update int_usable_arrivals set data_outage = false")
    con.close()
    dbt_run(full, run_vars, "int_forecast_grid+")
    return full


def check(db: Path, run_vars: str, count: int, complete: bool = False) -> int:
    with tempfile.TemporaryDirectory(prefix="headway-leakage-") as tmp:
        return _check(assume_complete(db, run_vars, Path(tmp)) if complete else db, run_vars, count)


def _check(db: Path, run_vars: str, count: int) -> int:
    times = pick_times(db, count)
    if not times:
        print("leakage check: no forecast examples to compare", file=sys.stderr)
        return 1
    compared = deleted_total = 0
    failed = False
    with tempfile.TemporaryDirectory(prefix="headway-leakage-") as tmp:
        for t in times:
            cut = Path(tmp) / "cut" / db.name
            cut.parent.mkdir(exist_ok=True)
            shutil.copyfile(db, cut)
            con = duckdb.connect(str(cut))
            for table, predicate in CUTS.items():
                where = predicate.format(t=f"timestamptz '{t.isoformat()}'")
                before = con.execute(f"select count(*) from {table}").fetchone()  # noqa: S608
                con.execute(f"delete from {table} where {where}")  # noqa: S608
                after = con.execute(f"select count(*) from {table}").fetchone()  # noqa: S608
                assert before is not None and after is not None
                deleted_total += before[0] - after[0]
            con.close()
            dbt_run(cut, run_vars, "int_forecast_line_features", "fct_forecast_examples")
            cols, full = features(db, t)
            _, rebuilt = features(cut, t)
            compared += len(full)
            if full != rebuilt:
                failed = True
                diff = [
                    (c, a, b)
                    for fr, rr in zip(full, rebuilt, strict=False)
                    for c, a, b in zip(cols, fr, rr, strict=True)
                    if a != b
                ]
                print(
                    f"LEAK at t={t}: {len(full)} rows full, {len(rebuilt)} rebuilt, "
                    f"first differences {diff[:5]}",
                    file=sys.stderr,
                )
    if failed:
        return 1
    if compared == 0 or deleted_total == 0:
        print(
            f"leakage check proved nothing: {compared} rows compared, {deleted_total} deleted",
            file=sys.stderr,
        )
        return 1
    print(
        f"leakage check passed: {len(times)} prediction times, {compared} examples compared, "
        f"{deleted_total} input rows cut"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("--vars", default="{}")
    parser.add_argument("--times", type=int, default=4)
    parser.add_argument("--assume-complete", action="store_true")
    args = parser.parse_args(argv)
    return check(args.db, args.vars, args.times, args.assume_complete)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
