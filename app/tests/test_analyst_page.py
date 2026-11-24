"""The AI analyst page: without a key (examples only), with a fake model, budgets."""

import json
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest
from withheld import rendered_text

from lib import analyst

APP = Path(__file__).resolve().parents[1]
PAGE = str(APP / "pages/8_AI_analyst.py")
CALL = {"line": "Red", "metric": "predicted_deviation_median_s", "window": "last_24h"}


class FakeChat:
    model = "fake"

    def __init__(self, text: str) -> None:
        self.text, self.turn = text, 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        self.turn += 1
        if self.turn == 1:
            call = {"id": "c1", "name": "get_line_reliability", "arguments": json.dumps(CALL)}
            return {"content": None, "tool_calls": [call]}
        return {"content": self.text, "tool_calls": []}


@pytest.fixture(autouse=True)
def isolated_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyst.budget, "default_path", lambda: tmp_path / "budget.json")
    analyst.daily_budget.clear()


def _run(root: Path, monkeypatch: pytest.MonkeyPatch, key: str | None) -> AppTest:
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(PAGE, default_timeout=60)
    if key:
        at.secrets[analyst.SECRET] = key
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _ask(at: AppTest, question: str) -> AppTest:
    at.text_input[0].set_value(question).run()
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_without_a_key_only_the_cached_examples_show(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run(export_root, monkeypatch, None)
    text = rendered_text(at)
    assert "Live questions are off" in text
    assert "precomputed from the tools" in text and "How reliable was the Red Line" in text
    assert not at.text_input and not at.button


def test_with_a_key_a_verified_answer_shows_badge_and_provenance(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        analyst, "make_client", lambda key: FakeChat("Red's median deviation was 3 s.")
    )
    at = _ask(_run(export_root, monkeypatch, "k"), "How is Red?")
    text = rendered_text(at)
    assert "verified: every number" in text
    assert "Red's median deviation was 3 s." in text
    for label in ("Metric definition", "Comparison period", "Coverage", "Freshness"):
        assert label in text
    assert any("select" in c.value for c in at.code)  # the SQL, in the sources expander


def test_an_unverifiable_answer_is_replaced(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analyst, "make_client", lambda key: FakeChat("Red was 999 s."))
    text = rendered_text(_ask(_run(export_root, monkeypatch, "k"), "How is Red?"))
    assert "could not verify" in text and "Red was 999 s." not in text


def test_session_limit_falls_back_to_the_examples(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(analyst.budget.ENV_SESSION, "1")
    monkeypatch.setattr(analyst, "make_client", lambda key: FakeChat("Red was 3 s."))
    at = _ask(_run(export_root, monkeypatch, "k"), "How is Red?")
    at.text_input[0].set_value("Again?").run()
    at.button[0].click().run()
    assert any("this session has used its questions" in w.value for w in at.warning)
    assert "precomputed" in rendered_text(at)


def test_daily_budget_falls_back_to_the_examples(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(analyst.budget.ENV_DAILY, "0")
    at = _ask(_run(export_root, monkeypatch, "k"), "How is Red?")
    assert any("today's question budget is spent" in w.value for w in at.warning)
    assert "precomputed" in rendered_text(at)
