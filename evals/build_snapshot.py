"""Build the frozen gold snapshot of the evals from a demo export.

Usage: python -m evals.build_snapshot [--src .local/demo-r2/public/boston/gold]

Copies the gold tables the analyst tools read (small Parquet only) and rewrites the manifest so it
lists exactly those tables. The result is committed under evals/snapshot/; run this only to rebuild
the frozen data (which changes every gold answer).
"""

import argparse
import json
import shutil
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent
SNAPSHOT = ROOT / "snapshot"
TABLES = (
    "agg_line_reliability_window",
    "agg_line_reliability_hourly",
    "agg_actual_deviation_window",
    "agg_actual_deviation_daily",
    "agg_actual_deviation_hourly",
    "agg_headway_window",
    "agg_headway_adherence_daily",
    "agg_headway_adherence_hourly",
    "agg_ett_window",
    "agg_ett_daily",
    "agg_active_alerts",
    "agg_pipeline_health",
)


def build(src: Path, out: Path = SNAPSHOT) -> dict[str, int]:
    gold = out / "gold"
    shutil.rmtree(out, ignore_errors=True)
    gold.mkdir(parents=True)
    manifest = json.loads((src / "manifest.json").read_text())
    counts: dict[str, int] = {}
    con = duckdb.connect()
    for table in TABLES:
        shutil.copy(src / f"{table}.parquet", gold / f"{table}.parquet")
        row = con.execute(f"select count(*) from read_parquet('{gold / table}.parquet')").fetchone()
        counts[table] = int(row[0]) if row else 0
    manifest["row_counts"] = counts
    (gold / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", type=Path, default=Path(".local/demo-r2/public/boston/gold"))
    args = parser.parse_args()
    for table, n in build(args.src).items():
        print(f"{table}: {n} rows")


if __name__ == "__main__":
    main()
