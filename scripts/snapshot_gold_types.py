"""Write web/src/data/gold-column-types.json: the real Parquet column type of every gold column.

Builds the demo (all releases on) and copies `column_types` from the gold export manifest. The
data contracts (web/scripts/build-contracts.mjs) read this file instead of guessing types from
column names; tests/unit/test_export_gold.py fails when it drifts from what dbt exports.

    uv run python scripts/snapshot_gold_types.py
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "src" / "data" / "gold-column-types.json"


def main() -> int:
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_demo

    with tempfile.TemporaryDirectory(prefix="headway-types-") as tmp:
        public = build_demo.build(Path(tmp), "samples/boston", "boston")
        manifest = json.loads((public / "gold" / "manifest.json").read_text())
    types = {t: manifest["column_types"][t] for t in sorted(manifest["column_types"])}
    OUT.write_text(json.dumps(types, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(types)} tables")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
