"""Write the two static geometry tables (``shape_points_dist``, ``stop_shape_positions``) into a
local copy of curated static data, with the static Lambda's own code (`headway.curate.geometry`).
Production only has them after the Release 2 switch-on step (docs/DEPLOY.md); the Release 2
measurements need them.

    uv run python scripts/synth_geometry.py --static .local/prod_copy/curated/boston/static
"""

import argparse
from pathlib import Path

import pyarrow.parquet as pq

from headway.curate.geometry import geometry_tables


def read(static: Path, name: str, version: str) -> object:
    files = sorted((static / f"table={name}" / f"feed_version={version}").glob("*.parquet"))
    return pq.ParquetFile(files[0]).read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", type=Path, required=True)
    args = ap.parse_args()
    versions = sorted(
        p.name.split("=", 1)[1] for p in (args.static / "table=trips").glob("feed_version=*")
    )
    for version in versions:
        tables = geometry_tables(
            *(read(args.static, n, version) for n in ("shapes", "stops", "trips", "stop_times")),  # type: ignore[arg-type]
            version,
        )
        for name, table in tables.items():
            out = args.static / f"table={name}" / f"feed_version={version}"
            out.mkdir(parents=True, exist_ok=True)
            pq.write_table(table, out / "part.parquet")
            print(f"{name} {version}: {table.num_rows} rows")  # noqa: T201


if __name__ == "__main__":
    main()
