"""Budgets (daily, per session, persisted) and the precomputed examples."""

import json
from datetime import UTC, datetime
from pathlib import Path

from headway_mcp import budget, examples
from headway_mcp.tools import Tools


def test_daily_budget_counts_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    daily = budget.DailyBudget(path, limit=2)
    assert daily.try_take() and daily.try_take() and not daily.try_take()
    again = budget.DailyBudget(path, limit=2)  # a restarted process reads the file
    assert again.used() == 2 and not again.try_take()


def test_daily_budget_resets_on_a_new_day(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    path.write_text(json.dumps({"date": "2001-01-01", "count": 99}))
    daily = budget.DailyBudget(path, limit=1)
    assert daily.used() == 0 and daily.try_take()
    assert json.loads(path.read_text())["date"] == datetime.now(UTC).date().isoformat()


def test_unwritable_counter_keeps_the_memory_count(tmp_path: Path) -> None:
    daily = budget.DailyBudget(tmp_path / "missing" / "b.json", limit=1)
    assert daily.try_take() and not daily.try_take()


def test_admit_checks_session_then_day(tmp_path: Path) -> None:
    daily = budget.DailyBudget(tmp_path / "b.json", limit=3)
    session = budget.SessionLimit(2)
    assert [budget.admit(daily, session) for _ in range(3)] == ["ok", "ok", "session"]
    assert daily.used() == 2  # the refused question was not counted
    other = budget.SessionLimit(5)
    assert [budget.admit(daily, other) for _ in range(2)] == ["ok", "daily"]


def test_defaults_and_env(monkeypatch: object) -> None:
    assert budget.DEFAULT_DAILY == 100 and budget.DEFAULT_SESSION == 10
    import pytest

    mp = pytest.MonkeyPatch()
    mp.setenv(budget.ENV_DAILY, "7")
    mp.setenv(budget.ENV_SESSION, "oops")
    try:
        assert budget.daily_budget() == 7 and budget.session_limit() == 10
    finally:
        mp.undo()


def test_examples_are_precomputed_from_the_tools(tools: Tools, tmp_path: Path) -> None:
    out = tmp_path / "analyst_examples.json"
    n = examples.write(tools, str(out))
    items = examples.load(json.loads(out.read_text()))
    assert n == len(items) >= 4
    first = items[0]
    assert first["answer"] and first["results"][0]["provenance"]["sql"]
    assert (
        "60 s" in first["answer"]
        or "withheld" in first["answer"]
        or "not enough" in first["answer"]
    )
    assert examples.load(None) == [] and examples.load({"schema": 99}) == []
