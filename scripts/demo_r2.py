"""Build the Release 2 demo from the real captures of the sampling spike and launch the app.

Usage: python scripts/demo_r2.py [--root .local/spike_root] [--out .local/demo-r2] [--no-launch]

`--root` is the curated root assembled around the spike's 27 hours of vehicle_positions and
trip_updates (`.local/spike`, see docs/spikes/2026-11-sampling.md): its `curated/<city>/` links the
spike tables next to the static GTFS, manifests and alerts. dbt builds every model, Release 2
included (HEADWAY_ENABLE_R2=true), without tests: the data is a research capture, not a pipeline
run. Gold is exported to <out>/public/<city>/gold, the HEADWAY_DATA_URL root. The demo has no
live feed, so live/vehicles_latest.json is an empty snapshot stamped with the end of the data.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBT = ["uv", "run", "--project", "dbt", "dbt"]
DBT_ARGS = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local"]


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    env.update(extra)
    return env


def build(root: Path, out: Path, city: str) -> Path:
    """Return <out>/public/<city>, the HEADWAY_DATA_URL root."""
    root = root.resolve()
    if not (root / "curated" / city).is_dir():
        raise SystemExit(
            f"no curated data at {root}/curated/{city}; see docs/spikes/2026-11-sampling.md"
        )
    out = out.resolve()
    public = out / "public" / city
    gold = public / "gold"
    live = public / "live"
    gold.mkdir(parents=True, exist_ok=True)
    live.mkdir(parents=True, exist_ok=True)
    db = out / "headway.duckdb"
    db.unlink(missing_ok=True)
    env = _env(HEADWAY_DUCKDB_PATH=str(db), HEADWAY_ENABLE_R2="true")
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
    as_of = json.loads((gold / "manifest.json").read_text())["metrics_as_of"]
    (live / "vehicles_latest.json").write_text(
        json.dumps(
            {
                "generated_at": as_of,
                "feed_header_ts": as_of,
                "vehicle_count": 0,
                "dropped_invalid": 0,
                "vehicles": [],
            }
        )  # fmt: skip
    )
    return public


def main(argv: list[str]) -> int:
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
