"""Build the Release 2 demo from the real captures of the sampling spike and launch the app.

Usage: python scripts/demo_r2.py [--root .local/spike_root] [--out .local/demo-r2] [--no-launch]

`--root` is the curated root assembled around the spike's 27 hours of vehicle_positions and
trip_updates (`.local/spike`, see docs/spikes/2026-11-sampling.md): its `curated/<city>/` links the
spike tables next to the static GTFS, manifests and alerts. dbt builds every model, Release 2
included (HEADWAY_ENABLE_R2=true, HEADWAY_ENABLE_R3=true), without tests: the data is a research
capture, not a pipeline run. Gold is exported to <out>/public/<city>/gold, the HEADWAY_DATA_URL
root. The demo has no live feed, so live/vehicles_latest.json is written from the latest vehicle
capture of the data.

The alerts table of the root is empty unless `.local/spike/alerts` exists; it is linked in when it
does (fetch it read-only, see docs/spikes/2026-11-sampling.md).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DBT = ["uv", "run", "--project", "dbt", "dbt"]
DBT_ARGS = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local"]


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    env.update(extra)
    return env


def link_alerts(root: Path, city: str, alerts: Path) -> bool:
    """Point the root's alerts table at `alerts` when it holds no rows yet. True when linked."""
    table = root / "curated" / city / "table=alerts"
    if not alerts.is_dir() or (
        table.is_dir()
        and not table.is_symlink()
        and any(p.name != "part-empty.parquet" for p in table.rglob("*.parquet"))
    ):
        return False
    if table.is_symlink():
        table.unlink()
    elif table.is_dir():
        for path in sorted(table.rglob("*"), reverse=True):
            path.rmdir() if path.is_dir() else path.unlink()
        table.rmdir()
    table.symlink_to(alerts.resolve())
    return True


def _iso(column: str) -> str:
    return f"strftime({column} at time zone 'UTC', '%Y-%m-%dT%H:%M:%S+00:00')"


def latest_snapshot(root: Path, city: str) -> dict[str, Any]:
    """The live snapshot of the newest vehicle capture in the curated vehicle_positions."""
    files = str(root / "curated" / city / "table=vehicle_positions" / "**" / "*.parquet")
    con = duckdb.connect()
    row = con.execute(
        f"select capture_id, {_iso('fetched_at')}, {_iso('feed_header_ts')} "
        f"from read_parquet('{files}') "
        "order by fetched_at desc limit 1"
    ).fetchone()
    if row is None:
        raise SystemExit("no vehicle positions in the curated root")
    capture_id, fetched_at, header_ts = row
    lines = ", ".join(f"'{r}'" for r in ("Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D",
                                        "Green-E", "Mattapan"))  # fmt: skip
    cur = con.execute(
        f"select vehicle_id, route_id, trip_id, direction_id, lat, lon, bearing, current_status, "
        f"stop_id, {_iso('event_ts')} as event_ts, is_stale_position, "
        "epoch(feed_header_ts) - epoch(event_ts) as position_age_s "
        f"from read_parquet('{files}') "
        f"where capture_id = ? and route_id in ({lines}) and vehicle_id is not null "
        "order by vehicle_id",
        [capture_id],
    )
    names = [d[0] for d in cur.description]
    vehicles = []
    for values in cur.fetchall():
        v = dict(zip(names, values, strict=True))
        v["is_stale_position"] = bool(v["is_stale_position"])
        vehicles.append(v)
    return {
        "generated_at": fetched_at,
        "feed_header_ts": header_ts,
        "vehicle_count": len(vehicles),
        "dropped_invalid": 0,
        "vehicles": vehicles,
    }


def build(root: Path, out: Path, city: str) -> Path:
    """Return <out>/public/<city>, the HEADWAY_DATA_URL root."""
    root = root.resolve()
    if not (root / "curated" / city).is_dir():
        raise SystemExit(
            f"no curated data at {root}/curated/{city}; see docs/spikes/2026-11-sampling.md"
        )
    if link_alerts(root, city, ROOT / ".local" / "spike" / "alerts"):
        print("alerts: linked .local/spike/alerts")
    out = out.resolve()
    public = out / "public" / city
    gold = public / "gold"
    live = public / "live"
    gold.mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    db = out / "headway.duckdb"
    db.unlink(missing_ok=True)
    env = _env(HEADWAY_DUCKDB_PATH=str(db), HEADWAY_ENABLE_R2="true", HEADWAY_ENABLE_R3="true")
    run_vars = json.dumps({"curated_root": str(root)})
    subprocess.run(
        [*DBT, "build", *DBT_ARGS, "--vars", run_vars,
         "--exclude", "resource_type:test", "resource_type:unit_test"],
        cwd=ROOT, env=env, check=True,
    )  # fmt: skip
    subprocess.run(
        [*DBT, "run-operation", "export_gold", *DBT_ARGS, "--vars", run_vars,
         "--args", f"{{target_dir: {gold}}}"],
        cwd=ROOT, env=env, check=True,
    )  # fmt: skip
    snapshot = latest_snapshot(root, city)
    (live / "vehicles_latest.json").write_text(json.dumps(snapshot))
    print(f"live snapshot: {snapshot['vehicle_count']} vehicles from {snapshot['generated_at']}")
    return public


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dbt_deps import ensure

    ensure()
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT / ".local" / "spike_root")
    parser.add_argument("--out", type=Path, default=ROOT / ".local" / "demo-r2")
    parser.add_argument("--city", default="boston")
    parser.add_argument("--no-launch", action="store_true")
    args = parser.parse_args(argv)
    public = build(args.root, args.out, args.city)
    print(f"demo data: {public}")
    if args.no_launch:
        return 0
    cmd = ["uv", "run", "--group", "app", "streamlit", "run", "app/Home.py"]
    return subprocess.run(cmd, cwd=ROOT, env=_env(HEADWAY_DATA_URL=f"file://{public}")).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
