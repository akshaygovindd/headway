"""End-to-end check: replay samples/boston, build the dbt project, compare key tables.

Runs inside the dbt environment (`uv run --project dbt python scripts/e2e.py`).
Use --update to rewrite tests/e2e/expected/*.csv after reviewing a change.
"""

import csv
import difflib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = ROOT / "tests" / "e2e" / "expected"
TABLES = [
    "agg_line_reliability_hourly",
    "agg_pipeline_health",
    "agg_quality_checks_hourly",
    "agg_arrival_reconstruction_daily",
    "agg_prediction_accuracy_daily",
    "agg_actual_deviation_hourly",
    "agg_headway_adherence_hourly",
    "agg_ett_daily",
    "agg_active_alerts",
]
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


def add_duplicate_deliveries(source: Path) -> None:
    """Add the two shapes a double scheduler delivery leaves, to a copy of the sample.

    - history: a second run for minute 14:50 with the same capture_ids (written before ingest
      claimed the minute); stg_manifests keeps one row per capture and the run is counted.
    - flagged: a duplicate-delivery record for minute 15:08, as ingest writes it today.
    """
    root = source / "manifests" / "boston"

    def clone(src: str, dst: Path, run_id: str, **extra: object) -> None:
        doc = json.loads((root / src).read_text())
        doc["run_id"] = run_id
        for entry in doc["entries"]:
            entry["run_id"] = run_id
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(json.dumps({**doc, **extra}, indent=2, sort_keys=True))

    hour14 = root / "dt=2026-11-22/hour=14"
    twin_id = "01M81VPPV5ZJ5QDPXP9DK7P4YR"
    clone(
        "dt=2026-11-22/hour=14/01M81VPPV5ZJ5QDPXP9DK7P4YQ.json", hour14 / f"{twin_id}.json", twin_id
    )
    original = "dt=2026-11-22/hour=15/01M81WSG450EHBGR58P51QZQGA.json"
    run_id = "01M81WSG450EHBGR58P51QZQGB"
    record = root / "duplicates/dt=2026-11-22/hour=15" / f"20261122T1508Z-{run_id}.json"
    clone(original, record, run_id, duplicate_of=f"manifests/boston/{original}")


def add_vehicle_polls(source: Path) -> None:
    """Turn minutes 15:20-15:29 into 15 s sampling: three more vehicle captures per minute.

    Each extra poll copies the minute's vehicle body under a new capture id, fetched 15, 30 and
    45 s after the first and numbered poll_index 1-3. Polls 1 and 2 get their own content hash
    (a feed that changed); poll 3 repeats poll 2's hash, so it is flagged as a duplicate capture.
    All share the minute's scheduled_at, so the minute still counts once and the run is one run.
    """
    root = source / "manifests" / "boston" / "dt=2026-11-22" / "hour=15"
    for path in sorted(root.glob("*.json")):
        doc = json.loads(path.read_text())
        poll0 = doc["entries"][0]
        scheduled = datetime.fromisoformat(poll0["scheduled_at"].replace("Z", "+00:00"))
        if not (20 <= scheduled.minute <= 29):
            continue
        vehicle = next(e for e in doc["entries"] if e["feed"] == "vehicle_positions")
        fetched = datetime.fromisoformat(vehicle["fetched_at"].replace("Z", "+00:00"))
        body = (source / vehicle["s3_key"]).read_bytes()
        for poll, hash_poll in ((1, 1), (2, 2), (3, 2)):
            at = fetched + timedelta(seconds=15 * poll)
            digest = f"{hash_poll:02d}{vehicle['content_hash'][2:]}"
            capture_id = f"vehicle_positions-{at:%Y%m%dT%H%M%SZ}-{digest[:8]}"
            key = f"raw/boston/feed=vehicle_positions/dt=2026-11-22/hour=15/{capture_id}.pb.gz"
            (source / key).write_bytes(body)
            doc["entries"].append(
                {
                    **vehicle,
                    "capture_id": capture_id,
                    "content_hash": digest,
                    "fetched_at": at.isoformat().replace("+00:00", "Z"),
                    "poll_index": poll,
                    "s3_key": key,
                }
            )
        path.write_text(json.dumps(doc, indent=2, sort_keys=True))


def main(argv: list[str]) -> int:
    update = "--update" in argv
    with tempfile.TemporaryDirectory(prefix="headway-e2e-") as tmp:
        source = Path(tmp) / "source"
        shutil.copytree(ROOT / "samples" / "boston", source)
        add_duplicate_deliveries(source)
        add_vehicle_polls(source)
        replay_dir = Path(tmp) / "replay"
        db = Path(tmp) / "e2e.duckdb"
        subprocess.run(
            [
                "uv", "run", "headway", "replay",
                "--source", str(source), "--target", str(replay_dir), "--city", "boston",
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
