"""Incremental closed-day build versus full rebuild: every table must be identical (ADR-024).

Builds the whole project twice over the same curated data (`headway replay` target or a copy of
the curated tree): once in full, once with the first ``--persist`` service days read from
persisted facts (written here in the production layout, ``<table>/metric_version=N/
service_date=D/part.parquet``) and the other days computed. Then compares every table of the two
databases row for row (``EXCEPT ALL`` both ways).

    uv run --project dbt python scripts/check_incremental.py \\
        --curated-root .local/spike_root --persist 1
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dbt"))
sys.path.insert(0, str(ROOT / "scripts"))

from headway_transform import facts_state  # noqa: E402

DBT = ["uv", "run", "--project", "dbt", "dbt"]
DBT_ARGS = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local"]
# Run bookkeeping and test-run output differ between any two builds by design. Nothing else is
# ignored: list columns are compared in their stored order, so an unordered list(distinct ...)
# fails here (see --twice, which proves two full builds are identical).
SKIP_PREFIXES = ("elementary", "dbt_", "agg_transform_runs", "stg_transform_runs")


def _env(db: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    env["HEADWAY_DUCKDB_PATH"] = str(db)
    return env


def build(db: Path, variables: dict[str, object]) -> None:
    proc = subprocess.run(
        [
            *DBT, "build", *DBT_ARGS, "--exclude", "resource_type:test", "resource_type:unit_test",
            "--vars", json.dumps(variables),
        ],
        cwd=ROOT, env=_env(db), check=False, capture_output=True, text=True,
    )  # fmt: skip
    if proc.returncode:
        print(proc.stdout[-6000:], proc.stderr[-2000:], file=sys.stderr)  # noqa: T201
        raise SystemExit("dbt build failed")


def persist(db: Path, facts_root: Path, days: list[str]) -> None:
    con = duckdb.connect(str(db), read_only=True)
    try:
        for table, version in facts_state.FACT_VERSIONS.items():
            for day in days:
                path = facts_root / facts_state.key_for(table, version, _date(day)).removeprefix(
                    facts_state.PREFIX + "/"
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                con.execute(
                    f"copy (select * from main.{table} where service_date = date '{day}') "  # noqa: S608
                    f"to '{path}' (format parquet)"
                )
    finally:
        con.close()


def _date(day: str):  # type: ignore[no-untyped-def]
    from datetime import date

    return date.fromisoformat(day)


def service_days(db: Path) -> list[str]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute("select distinct service_date from main.fct_stop_predictions order by 1")
        return [r[0].isoformat() for r in rows.fetchall()]
    finally:
        con.close()


def compare(full: Path, inc: Path) -> list[str]:
    """Differences, one line per table that is not identical.

    Each file is attached under its own name (dbt's views are qualified with it).
    """
    con = duckdb.connect()
    sides = {full.stem: full, inc.stem: inc}
    f_, i_ = full.stem, inc.stem
    for alias, path in sides.items():
        con.execute(f"attach '{path}' as {alias} (read_only)")
    tables = {
        alias: {
            r[0]
            for r in con.execute(
                "select table_name from information_schema.tables where table_catalog = ? "
                "and table_schema = 'main' and table_type = 'BASE TABLE'",
                [alias],
            ).fetchall()
        }
        for alias in sides
    }
    problems: list[str] = []
    for name in sorted(tables[f_] | tables[i_]):
        if name.startswith(SKIP_PREFIXES):
            continue
        if name not in tables[f_] or name not in tables[i_]:
            problems.append(f"{name}: only in {'full' if name in tables[f_] else 'incremental'}")
            continue
        cols = ", ".join(
            c
            for c, _t in con.execute(
                f"select column_name, data_type from information_schema.columns "  # noqa: S608
                f"where table_catalog = '{f_}' and table_name = '{name}' order by ordinal_position"
            ).fetchall()
        )
        count = {
            a: con.execute(f"select count(*) from {a}.main.{name}").fetchone()[0]  # type: ignore[index]  # noqa: S608
            for a in sides
        }
        diff = {
            (a, b): con.execute(
                f"select count(*) from (select {cols} from {a}.main.{name} except all "  # noqa: S608
                f"select {cols} from {b}.main.{name})"
            ).fetchone()[0]  # type: ignore[index]
            for a, b in ((f_, i_), (i_, f_))
        }
        if any(diff.values()) or count[f_] != count[i_]:
            problems.append(
                f"{name}: full {count[f_]} rows, incremental {count[i_]}, "
                f"{diff[(f_, i_)]} / {diff[(i_, f_)]} differ"
            )
    con.close()
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curated-root", type=Path, required=True)
    ap.add_argument("--persist", type=int, default=1, help="how many of the first days to persist")
    ap.add_argument(
        "--twice", action="store_true",
        help="determinism proof: build the project twice in full and compare the two builds",
    )  # fmt: skip
    ap.add_argument(
        "--tamper", action="store_true",
        help="negative control: empty a persisted partition, the comparison must then fail",
    )  # fmt: skip
    args = ap.parse_args()
    from dbt_deps import ensure

    ensure()
    with tempfile.TemporaryDirectory(prefix="headway-incremental-") as tmp:
        work = Path(tmp)
        base = {"curated_root": str(args.curated_root.resolve())}
        full, inc = work / "fullbuild.duckdb", work / "incbuild.duckdb"
        build(full, base)
        if args.twice:
            build(inc, base)
            problems = compare(full, inc)
            for line in problems:
                print("DIFF", line, file=sys.stderr)  # noqa: T201
            print("two full builds identical" if not problems else "two full builds DIFFER")  # noqa: T201
            return 1 if problems else 0
        days = service_days(full)
        persisted, compute = days[: args.persist], days[args.persist :]
        facts_root = work / "facts"
        persist(full, facts_root, persisted)
        if args.tamper:
            tampered = facts_root / facts_state.key_for(
                "fct_headways", 1, _date(persisted[0])
            ).removeprefix(facts_state.PREFIX + "/")
            duckdb.execute(
                f"copy (select * from read_parquet('{tampered}') where false) "  # noqa: S608
                f"to '{tampered}.new' (format parquet)"
            )
            os.replace(f"{tampered}.new", tampered)
        build(
            inc,
            {
                **base,
                "compute_days": compute,
                "persisted_days": persisted,
                "facts_root": str(facts_root),
                "fact_versions": dict(facts_state.FACT_VERSIONS),
            },
        )
        problems = compare(full, inc)
    print(f"service days {days}: persisted {persisted}, computed {compute}")  # noqa: T201
    for line in problems:
        print("DIFF", line, file=sys.stderr)  # noqa: T201
    print("incremental == full" if not problems else "incremental DIFFERS from full")  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
