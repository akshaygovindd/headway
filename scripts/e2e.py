"""End-to-end check: replay samples/boston, build the dbt project, compare key tables.

Runs inside the dbt environment (`uv run --project dbt python scripts/e2e.py`).
Use --update to rewrite tests/e2e/expected/*.csv after reviewing a change.
"""

import csv
import difflib
import io
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tests" / "e2e" / "expected"
TABLES = ["agg_line_reliability_hourly", "agg_pipeline_health", "agg_quality_checks_hourly"]
DBT = ["uv", "run", "--project", "dbt", "dbt"]
DBT_ARGS = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local"]


def _clean_env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    env.update(extra)
    return env


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{round(value, 3):.3f}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def export(db: Path, table: str) -> str:
    con = duckdb.connect(str(db), read_only=True)
    con.execute("set TimeZone = 'UTC'")
    cur = con.execute(f"select * from {table} order by all")  # noqa: S608
    header = [d[0] for d in cur.description]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(header)
    for row in cur.fetchall():
        writer.writerow([_cell(v) for v in row])
    con.close()
    return buf.getvalue()


def main(argv: list[str]) -> int:
    update = "--update" in argv
    with tempfile.TemporaryDirectory(prefix="headway-e2e-") as tmp:
        replay_dir = Path(tmp) / "replay"
        db = Path(tmp) / "e2e.duckdb"
        subprocess.run(
            [
                "uv", "run", "headway", "replay",
                "--source", "samples/boston", "--target", str(replay_dir), "--city", "boston",
            ],  # fmt: skip
            cwd=ROOT, env=_clean_env(), check=True, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        env = _clean_env(HEADWAY_DUCKDB_PATH=str(db))
        subprocess.run(
            [*DBT, "build", *DBT_ARGS, "--vars", f'{{"curated_root": "{replay_dir}"}}'],
            cwd=ROOT, env=env, check=True,
        )  # fmt: skip
        subprocess.run([sys.executable, "scripts/check_dbt_docs.py"], cwd=ROOT, env=env, check=True)
        failed = False
        for table in TABLES:
            actual = export(db, table)
            path = EXPECTED / f"{table}.csv"
            if update:
                EXPECTED.mkdir(parents=True, exist_ok=True)
                path.write_text(actual)
                print(f"wrote {path.relative_to(ROOT)}")
                continue
            expected = path.read_text() if path.exists() else ""
            if actual != expected:
                failed = True
                diff = difflib.unified_diff(
                    expected.splitlines(), actual.splitlines(), "expected", "actual", lineterm=""
                )
                print(f"MISMATCH {table}", file=sys.stderr)
                print("\n".join(list(diff)[:60]), file=sys.stderr)
        if failed:
            return 1
    print("e2e passed" if not update else "expected files updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
