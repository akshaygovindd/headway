"""Write the API's OpenAPI schema, committed beside the TypeScript types generated from it.

The site's types (web/src/lib/types.ts) are checked against web/src/lib/api-types.ts, generated from
this file by ``npm run api-types``, in web/test/api-contract.test.ts (run by the typecheck), so a
change to a response shape that the site does not follow fails web CI. ``--check`` fails when the
committed file is stale.

    uv run python scripts/export_openapi.py
    uv run python scripts/export_openapi.py --check
"""

import json
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "src" / "lib" / "openapi.json"


def schema_text() -> str:
    from headway_api.app import create_app
    from headway_api.settings import Settings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        app = create_app(Settings(data_url="file:///nonexistent", cache_dir=None))
    return json.dumps(app.openapi(), indent=1, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    text = schema_text()
    if "--check" in argv:
        if not OUT.exists() or OUT.read_text() != text:
            print("web/src/lib/openapi.json is stale: run `npm run api-types` in web/")
            return 1
        print("openapi.json matches the API")
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
