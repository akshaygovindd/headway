"""Build the demo data tree: replay the sample, build dbt, export gold, write the live snapshot.

Usage: python scripts/build_demo.py <out_dir> [--source samples/boston] [--city boston]

Writes <out_dir>/public/<city>/{gold,live}, the layout HEADWAY_DATA_URL points at.
Only runs subprocesses, so it works from any environment that has `uv`.
"""

import argparse
import os
import shutil
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


def build(
    out: Path, source: str, city: str, enable_r2: bool = True, enable_r4: bool = True
) -> Path:
    """Return <out>/public/<city>, the HEADWAY_DATA_URL root.

    enable_r2 sets HEADWAY_ENABLE_R2: the Release 2 aggregates are exported only then. enable_r4
    (with R2) adds the Release 4 site tables; R3 follows R2.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from dbt_deps import ensure

    ensure()
    out = out.resolve()
    replay_dir = out / "replay"
    public = out / "public" / city
    for path in (replay_dir, public):
        shutil.rmtree(path, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "headway.duckdb").unlink(missing_ok=True)
    gold = public / "gold"
    gold.mkdir(parents=True)

    def run(cmd: list[str], **extra: str) -> None:
        subprocess.run(cmd, cwd=ROOT, env=_env(**extra), check=True, stdout=subprocess.DEVNULL)

    run(["uv", "run", "headway", "replay", "--source", source, "--target", str(replay_dir),
         "--city", city])  # fmt: skip
    env = {
        "HEADWAY_DUCKDB_PATH": str(out / "headway.duckdb"),
        "HEADWAY_ENABLE_R2": "true" if enable_r2 else "false",
        "HEADWAY_ENABLE_R3": "true" if enable_r2 else "false",
        "HEADWAY_ENABLE_R4": "true" if enable_r2 and enable_r4 else "false",
    }
    run_vars = f'{{"curated_root": "{replay_dir}"}}'
    run([*DBT, "build", *DBT_ARGS, "--vars", run_vars], **env)
    run([*DBT, "run-operation", "export_gold", *DBT_ARGS, "--vars", run_vars,
         "--args", f"{{target_dir: {gold}}}"], **env)  # fmt: skip
    run(["uv", "run", "headway", "live-snapshot", "--source", source, "--out", str(public / "live"),
         "--city", city])  # fmt: skip
    if enable_r2:
        sys.path.insert(0, str(ROOT / "src"))
        from headway_mcp import examples
        from headway_mcp.tools import Tools

        examples.write(Tools(f"file://{public}"), str(gold / "analyst_examples.json"))
    return public


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--source", default="samples/boston")
    parser.add_argument("--city", default="boston")
    args = parser.parse_args(argv)
    print(build(args.out, args.source, args.city))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
