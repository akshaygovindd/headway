"""Freeze a small real gold export as the committed snapshot the web fixtures are generated from.

Keeps the tables the public API serves (``headway_api.queries.API_TABLES``) of an export (a
``gold/`` folder with ``manifest.json``: a transform run's output, ``make demo-r2``, or a build of a
read-only copy of the curated data) under ``tests/fixtures/gold_snapshot/gold``. Public data, so
nothing is selected or anonymised. Rebuild the web fixtures afterwards: ``npm run fixtures``.

    uv run python scripts/export_gold_snapshot.py .local/prod_gold
"""

import json
import shutil
import sys
from pathlib import Path

import duckdb

from headway_api import queries

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "tests" / "fixtures" / "gold_snapshot" / "gold"


def export(src: Path, dest: Path = DEST) -> list[str]:
    manifest = json.loads((src / "manifest.json").read_text())
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    kept: list[str] = []
    for table in sorted(queries.API_TABLES):
        path = src / f"{table}.parquet"
        if not path.exists():
            continue
        duckdb.execute(
            f"copy (select * from read_parquet('{path}')) to '{dest / path.name}' "  # noqa: S608
            "(format parquet, compression zstd)"
        )
        kept.append(table)
    manifest["row_counts"] = {t: manifest["row_counts"][t] for t in kept}
    manifest["column_types"] = {t: manifest["column_types"][t] for t in kept}
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return kept


if __name__ == "__main__":
    tables = export(Path(sys.argv[1]))
    print(f"snapshot of {len(tables)} tables in {DEST}")
