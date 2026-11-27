"""Write the web fixtures by calling the real API over the committed real gold snapshot.

Every file in ``web/test/fixtures/real`` is what ``headway_api`` answers (FastAPI TestClient, the
same code the Lambda runs) for one request over ``tests/fixtures/gold_snapshot``, so a fixture
cannot drift from the API: when the API changes, ``--check`` fails until the fixtures are rebuilt.

Only two things are touched after the API answers, because they depend on when the API is called:
``freshness.age_minutes`` (set to a fixed 7.0) and ``provenance.served_from`` ("remote").
``web/test/fixtures/synthetic`` holds the few hand-made edge states the snapshot lacks (see its
README); the mock API serves a synthetic file in place of the real one with the same request.

    uv run python scripts/build_web_fixtures.py            # rewrite
    uv run python scripts/build_web_fixtures.py --check    # fail when the files are stale
"""

import json
import sys
import warnings
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from headway_api.app import create_app
from headway_api.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "tests" / "fixtures" / "gold_snapshot"
OUT = ROOT / "web" / "test" / "fixtures" / "real"
LINES = ["Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D", "Green-E", "Mattapan"]
WINDOWS = [None, "last_24h", "last_7d"]
# Station pairs the budget page and its tests ask for (Red); any other pair is a 422 like the API.
BUDGET_PAIRS = [
    ("place-davis", "place-harsq"),
    ("place-alfcl", "place-brntn"),
    ("place-asmnl", "place-brntn"),
]


def slug(path: str, params: dict[str, str]) -> str:
    """File name of a request: the path under /v1 and its sorted query, one string the mock API
    computes the same way (web/scripts/mock-api.mjs)."""
    name = path.removeprefix("/v1/").replace("/", "__")
    for k in sorted(params):
        name += f"--{k}={params[k]}"
    return name


def requests() -> list[tuple[str, dict[str, str]]]:
    out: list[tuple[str, dict[str, str]]] = [("/v1/lines", {})]
    for line in LINES:
        base = f"/v1/lines/{line}"
        for what in ("reliability", "headways", "accuracy", "countdown", "trip-time"):
            out += [(f"{base}/{what}", {"window": w} if w else {}) for w in WINDOWS]
        for w in WINDOWS:
            for d in (None, "0", "1"):
                params = {**({"window": w} if w else {}), **({"direction": d} if d else {})}
                out.append((f"{base}/segments", params))
        out += [(f"{base}/marey", {"direction": d} if d else {}) for d in (None, "0", "1")]
        out += [(f"{base}/spacing-by-hour", {}), (f"{base}/disruptions", {})]
        out.append((f"{base}/trip-time-budget", {}))
        out.append((f"/v1/forecast/{line}", {}))
        for w in ("last_24h", "last_7d"):
            out.append(("/v1/accessibility", {"line": line, "window": w}))
            out.append(("/v1/slow-zones", {"line": line, "window": w}))
    for w in (None, "last_24h", "last_7d"):
        out.append(("/v1/accessibility", {"window": w} if w else {}))
        out.append(("/v1/slow-zones", {"window": w} if w else {}))
    for a, b in BUDGET_PAIRS:
        for w in WINDOWS:
            params = {"from": a, "to": b, **({"window": w} if w else {})}
            out.append(("/v1/lines/Red/trip-time-budget", params))
    out += [("/v1/incidents", {}), ("/v1/slo", {}), ("/v1/metrics", {}), ("/v1/meta", {})]
    out += [("/v1/pipeline/health", {"days": "7"}), ("/v1/pipeline/health", {})]
    out += [("/v1/pipeline/heartbeat", {"hours": "24"}), ("/v1/pipeline/heartbeat", {})]
    out += [("/v1/pipeline/checks", {"hours": "24"}), ("/v1/pipeline/checks", {})]
    return out


def _normalise(body: Any) -> Any:
    if isinstance(body, dict):
        if isinstance(body.get("freshness"), dict) and "age_minutes" in body["freshness"]:
            body["freshness"]["age_minutes"] = 7.0
        if isinstance(body.get("provenance"), dict) and "served_from" in body["provenance"]:
            body["provenance"]["served_from"] = "remote"
    return body


def build() -> dict[str, str]:
    """File name -> text, for every request."""
    from fastapi.testclient import TestClient

    settings = Settings(data_url=SNAPSHOT.as_uri(), cache_dir=None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        client = TestClient(create_app(settings))
        files: dict[str, str] = {}
        for path, params in requests():
            res = client.get(path, params=params)
            query = urlencode(params)
            doc = {
                "request": f"GET {path}" + (f"?{query}" if query else ""),
                "status": res.status_code,
                "body": _normalise(res.json()),
            }
            files[slug(path, params) + ".json"] = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    return files


def main(argv: list[str]) -> int:
    files = build()
    if "--check" in argv:
        stale = sorted(
            n
            for n, text in files.items()
            if not (OUT / n).exists() or (OUT / n).read_text() != text
        )
        stale += sorted(p.name for p in OUT.glob("*.json") if p.name not in files)
        if stale:
            print(f"{len(stale)} fixture files are stale (run npm run fixtures): {stale[:5]}")
            return 1
        print(f"{len(files)} fixtures match the API")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for name, text in files.items():
        (OUT / name).write_text(text)
    print(f"wrote {len(files)} fixtures to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
