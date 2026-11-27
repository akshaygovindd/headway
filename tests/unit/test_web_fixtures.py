"""The web fixtures are the real API's answers over the committed real gold snapshot, and they obey
the API's data rules (docs/DATA_QUALITY.md)."""

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_web_fixtures as bwf  # noqa: E402

FIXTURES = bwf.OUT.parent
VALUE_PREFIXES = ("pct_", "median_", "p90_", "p95_", "mae_", "buffer_time_index")


def test_real_fixtures_match_what_the_api_answers() -> None:
    files = bwf.build()
    stale = [
        n
        for n, text in files.items()
        if not (bwf.OUT / n).exists() or (bwf.OUT / n).read_text() != text
    ]
    stale += [p.name for p in bwf.OUT.glob("*.json") if p.name not in files]
    assert not stale, f"run `npm run fixtures` in web/: stale {sorted(stale)[:5]}"


def _rows(node: Any) -> Any:
    if isinstance(node, dict):
        if "coverage_pct" in node and "withheld_reason" in node:
            yield node
        for v in node.values():
            yield from _rows(v)
    elif isinstance(node, list):
        for v in node:
            yield from _rows(v)


def _fixture_files() -> list[Path]:
    return sorted(p for p in FIXTURES.rglob("*.json") if not p.name.startswith("_"))


def test_every_fixture_is_an_api_answer_or_labelled_synthetic() -> None:
    for path in _fixture_files():
        doc = json.loads(path.read_text())
        assert {"request", "status", "body"} <= set(doc), path
        if "synthetic" in path.parts:
            assert str(doc.get("_synthetic", "")).startswith("SYNTHETIC"), path
        else:
            assert "_synthetic" not in doc, path


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_withheld_rows_carry_a_reason_and_no_value(path: Path) -> None:
    for row in _rows(json.loads(path.read_text())["body"]):
        cov = row["coverage_pct"]
        if cov is not None:
            assert 0 <= cov <= 100, row
            if cov < 50:
                # a band with no trips at all says so in its own field instead
                reason = row["withheld_reason"] or (
                    row.get("status") == "no_trips" and row.get("no_trips_reason")
                )
                assert reason, f"coverage {cov} is under 50 with no reason: {row}"
        if row["withheld_reason"]:
            shown = {k: v for k, v in row.items() if k.startswith(VALUE_PREFIXES) and v is not None}
            assert not shown, f"withheld row still carries values {shown}"
        for k, v in row.items():
            if k.startswith("pct_") and v is not None:
                assert 0 <= v <= 100, (k, row)
