"""The eval scorer, tested with a scripted model: no network, no key."""

import json
import socket
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from evals import fake, run

from headway_mcp.tools import TOOL_NAMES

CATEGORIES = {
    "metric_lookup": 8,
    "line_comparison": 6,
    "specific_date": 5,
    "insufficient_data": 5,
    "causal_trap": 5,
    "service_vs_data_outage": 4,
    "metric_not_computed": 4,
    "out_of_scope_injection": 3,
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*a: Any, **k: Any) -> None:
        raise AssertionError("network access in a test")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)


def _go(inject: bool = False, max_cost: float = 0.5) -> tuple[list[run.QResult], list[str]]:
    return run.run(run.load_questions(), run.fake_clients(inject), max_cost=max_cost)


def test_questions_and_snapshot() -> None:
    entries = run.load_questions()
    assert Counter(e["category"] for e in entries) == CATEGORIES
    assert len({e["id"] for e in entries}) == 40
    manifest = json.loads((run.SNAPSHOT / "gold" / "manifest.json").read_text())
    for table in manifest["row_counts"]:
        assert (run.SNAPSHOT / "gold" / f"{table}.parquet").is_file()
    for e in entries:
        gold = run.compute_gold(e)
        if e["category"] == "insufficient_data":
            assert gold.withheld and not gold.values, e["id"]
            assert e["status"] == "insufficient_data"
        elif e["behaviour"] == "answer" or e.get("gold"):
            assert gold.values or gold.mentions, e["id"]
        assert set(e["tools"]) <= set(TOOL_NAMES), e["id"]


def test_honest_fake_run_meets_every_threshold() -> None:
    results, skipped = _go()
    summary = run.summarize(results, skipped=skipped, model=fake.MODEL, total=40)
    assert summary["ok"], summary["failed_thresholds"]
    assert summary["numeric_pct"] == 100.0 and summary["unsupported"] == 0
    assert summary["strict_refusal_pct"] == 100.0
    assert all(r.passed for r in results)


def test_injection_counts_as_a_correct_refusal_without_the_model() -> None:
    results, _ = _go()
    inj = next(r for r in results if r.id == "oi01")
    assert inj.status == "refused" and inj.llm_turns == 0
    assert inj.checks["refusal"] is True and inj.passed


def test_the_scorer_catches_a_wrong_number_and_a_wrong_direction() -> None:
    results, skipped = _go(inject=True)
    by_id = {r.id: r for r in results}
    assert {r.id for r in results if not r.passed} == set(fake.FAULTS)
    # a number that is in the results but is not the answer: the gate passes it, the gold does not
    wrong = by_id["ml01"]
    assert wrong.status == "verified" and wrong.checks["numeric"] is False
    # an invented number and a wrong direction: the gate blocks them, the draft is counted
    assert any("number not in evidence" in p for p in by_id["lc01"].problems)
    assert any("direction" in p for p in by_id["lc06"].problems)
    assert by_id["lc01"].status == by_id["lc06"].status == "could_not_verify"
    summary = run.summarize(results, skipped=skipped, model=fake.MODEL, total=40)
    assert not summary["ok"] and summary["unsupported"] == 2
    assert summary["shipped_unsupported"] == 0
    assert any("numeric" in f for f in summary["failed_thresholds"])


def test_cost_guard_stops_the_run_and_fails_it() -> None:
    results, skipped = _go(max_cost=0.003)
    assert 0 < len(results) < 40 and len(results) + len(skipped) == 40
    summary = run.summarize(results, skipped=skipped, model=fake.MODEL, total=40)
    assert summary["incomplete"] and not summary["ok"]


def test_main_exit_codes_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run.main([]) == 2  # no key and no --fake: nothing runs
    assert "OPENAI_API_KEY" in capsys.readouterr().err
    out = tmp_path / "r.md"
    assert run.main(["--fake", "--report", str(out)]) == 0
    assert "FAKE RUN" in out.read_text()
    assert run.main(["--fake", "--inject-faults", "--report", str(out)]) == 1
    text = out.read_text()
    assert "### ml01" in text and "### lc01" in text and "### lc06" in text
    assert "number not in evidence" in text and '"sql"' in text  # verbatim, with tool results


def test_percentile_and_cost() -> None:
    assert run.percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0], 90) == 9.0
    assert run.percentile([], 90) == 0.0
    assert run.cost_of("gpt-4o-mini", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert run.cost_of("unknown-model", 10, 10) is None
