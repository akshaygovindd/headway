"""The OpenAPI schema types every route's answer, and the real answers fit it."""

import json
import re
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from headway_api import schemas

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import export_openapi  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[2] / "web" / "test" / "fixtures"


def _route(request: str) -> str:
    path = request.split(" ")[1].split("?")[0]
    path = re.sub(r"/lines/[^/]+/", "/lines/{line}/", path)
    return re.sub(r"/forecast/[^/]+", "/forecast/{line}", path)


def _answers() -> list[Path]:
    return sorted(
        p
        for p in FIXTURES.rglob("*.json")
        if not p.name.startswith("_") and json.loads(p.read_text())["status"] == 200
    )


@pytest.mark.parametrize("path", _answers(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_answer_fits_its_schema(path: Path) -> None:
    doc = json.loads(path.read_text())
    TypeAdapter(schemas.Envelope[schemas.ROUTE_DATA[_route(doc["request"])]]).validate_python(
        doc["body"]
    )


def test_every_v1_route_with_data_is_typed() -> None:
    schema = json.loads(export_openapi.schema_text())
    untyped = [
        p
        for p in schema["paths"]
        if p.startswith("/v1") and p not in schemas.ROUTE_DATA and "{" not in p.split("/v1")[1][:0]
    ]
    assert untyped == [], f"routes with no response schema: {untyped}"


def test_committed_openapi_is_current() -> None:
    assert export_openapi.OUT.read_text() == export_openapi.schema_text(), (
        "run `npm run api-types` in web/"
    )
