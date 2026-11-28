"""The Release 2 acceptance checks decide correctly on fakes: no AWS, no network."""

import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import acceptance_r2 as r2  # noqa: E402

T0 = datetime(2026, 11, 26, 20, 0, tzinfo=UTC)


def minutes(n: int, skip: set[int] | None = None) -> set[datetime]:
    return {T0 + timedelta(minutes=i) for i in range(n) if i not in (skip or set())}


def test_soak_passes_with_all_minutes_over_72_hours() -> None:
    state, detail = r2.soak_missed(minutes(72 * 60 + 1), T0)[0]
    assert state == "PASS", detail


def test_soak_fails_above_one_percent_missed() -> None:
    skip = set(range(0, 72 * 60, 50))  # 2 % missing
    assert r2.soak_missed(minutes(72 * 60 + 1, skip), T0)[0][0] == "FAIL"


def test_soak_is_manual_until_72_hours_exist_since_switch_on() -> None:
    assert r2.soak_missed(minutes(10 * 60), T0)[0][0] == "MANUAL"


def test_soak_does_not_count_minutes_before_switch_on() -> None:
    since = T0 + timedelta(hours=10)
    state, detail = r2.soak_missed(minutes(72 * 60 + 600 + 1, set(range(0, 590))), since)[0]
    assert state == "PASS", detail


def test_switch_on_is_the_first_slot_with_r2_enabled() -> None:
    rows = [
        {"slot": T0 - timedelta(hours=1), "r2_enabled": False},
        {"slot": T0 + timedelta(hours=1), "r2_enabled": True},
        {"slot": T0, "r2_enabled": True},
    ]
    assert r2.switch_on_from_runs(rows) == T0
    assert r2.switch_on_from_runs([]) is None


def run(slot: int, status: str = "success", published: bool = True) -> dict[str, Any]:
    return {"slot": T0 + timedelta(hours=slot), "status": status, "gold_published": published}


def test_transform_success_counts_a_slot_good_if_any_run_succeeded() -> None:
    rows = [run(0, "tests_failed"), run(0)] + [run(h) for h in range(1, 100)]
    assert r2.transform_success(rows, T0)[0] == "PASS"


def test_transform_success_fails_below_99_percent_and_names_slots() -> None:
    rows = [run(h) for h in range(72)] + [run(72, "tests_failed")]
    state, detail = r2.transform_success(rows, T0)
    assert state == "FAIL" and "failed:" in detail


def test_transform_success_ignores_unpublished_runs() -> None:
    assert r2.transform_success([run(0, published=False)], T0)[0] == "FAIL"


def test_budget_limits() -> None:
    assert r2.budget(276, 65.4)[0] == "PASS"
    assert r2.budget(601, 65)[0] == "FAIL"
    assert r2.budget(300, 86)[0] == "FAIL"
    assert r2.budget(None, 50)[0] == "MANUAL"


def history_item(name: str, state: str) -> dict[str, Any]:
    return {
        "AlarmName": name,
        "Timestamp": T0,
        "HistoryData": json.dumps({"newState": {"stateValue": state}}),
    }


def test_alarm_transitions_keep_only_entries_into_alarm() -> None:
    items = [history_item("a", "ALARM"), history_item("a", "OK"), {"AlarmName": "b"}]
    assert [n for n, _ in r2.alarm_transitions(items)] == ["a"]


def test_known_alarm_cause_passes_and_is_listed() -> None:
    trans = [("headway-data-tests-failed", T0)] * 3
    state, detail = r2.judge_alarms(trans, r2.KNOWN_CAUSES)
    assert state == "PASS" and "x3" in detail and "4825cc5" in detail


def test_unexplained_alarm_fails_and_says_so() -> None:
    state, detail = r2.judge_alarms([("headway-gold-stale", T0)], r2.KNOWN_CAUSES)
    assert state == "FAIL" and "headway-gold-stale x1: UNEXPLAINED" in detail
    assert r2.judge_alarms([], r2.KNOWN_CAUSES)[0] == "PASS"


class FakePaginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages

    def paginate(self, **_: Any) -> list[dict[str, Any]]:
        return self.pages


class FakeCloudWatch:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items

    def get_paginator(self, name: str) -> FakePaginator:
        assert name == "describe_alarm_history"
        return FakePaginator([{"AlarmHistoryItems": self.items}])


def test_b12_combines_soak_and_alarm_history() -> None:
    now = T0 + timedelta(hours=80)
    soak = minutes(72 * 60 + 1)
    cw = FakeCloudWatch([history_item("headway-data-tests-failed", "ALARM")])
    state, detail = r2.b12(soak, T0, "test", cw, now)
    assert state == "PASS" and "countdown coverage" in detail
    cw = FakeCloudWatch([history_item("headway-feed-errors", "ALARM")])
    assert r2.b12(soak, T0, "test", cw, now)[0] == "FAIL"
    assert r2.b12(soak, T0, "test", None, now)[0] == "MANUAL"


def key(table: str, day: str, version: int = 4) -> str:
    return (
        f"public/boston/history/table={table}/metric_version={version}"
        f"/service_date={day}/part.parquet"
    )


def test_history_needs_every_closed_day_for_every_table() -> None:
    keys = [key(t, f"2026-11-2{d}") for t in ("a", "b") for d in (6, 7)]
    assert r2.history_gaps(keys, date(2026, 11, 26), date(2026, 11, 27))[0] == "PASS"
    state, detail = r2.history_gaps(keys[:-1], date(2026, 11, 26), date(2026, 11, 27))
    assert state == "FAIL" and "b: 1 missing" in detail
    assert r2.history_gaps([], date(2026, 11, 26), date(2026, 11, 27))[0] == "FAIL"


def test_history_looks_only_at_the_newest_metric_version() -> None:
    keys = [key("a", "2026-11-26", 1), key("a", "2026-11-26", 4)]
    assert r2.history_gaps(keys, date(2026, 11, 26), date(2026, 11, 26))[0] == "PASS"
    assert r2.history_gaps(keys, date(2026, 11, 25), date(2026, 11, 26))[0] == "FAIL"


DRILL = """## Drills

### DRILL 2026-11-29: feed_down for 15 min

- **Window:** 10:00-10:15Z
- **Observed:** detect: summary lines all error; warn: feed_gap_min fail; label: hour data_outage;
  alarm: headway-feed-errors at 10:06; recover: clean minute 10:16.
- **Recovery:** ok
"""


def test_drill_must_record_all_five_stages() -> None:
    assert r2.judge_drill(r2.parse_drills(DRILL))[0] == "PASS"
    partial = DRILL.replace("alarm: headway-feed-errors at 10:06; ", "")
    state, detail = r2.judge_drill(r2.parse_drills(partial))
    assert state == "MANUAL" and "alarm" in detail


def test_no_drill_entry_fails() -> None:
    assert r2.judge_drill(r2.parse_drills("## Drills\n\nNo drill has been run yet."))[0] == "FAIL"
    template = "### DRILL YYYY-MM-DD: <mode> for <minutes> min\n"
    assert r2.parse_drills(template) == []


def test_alarm_runbook_links_must_resolve() -> None:
    runbook = "### Alarm: headway-a\n\ntext\n### Alarm: headway-b\n"
    base = "https://h/RUNBOOK.md#"
    tf = (
        f'alarm_name = "headway-a"\nalarm_description = "x. Runbook: {base}alarm-headway-a"\n'
        f'alarm_name = "headway-b"\nalarm_description = "x. Runbook: {base}alarm-headway-zzz"\n'
        'alarm_name = "headway-c"\nalarm_description = "no link"\n'
    )
    assert r2.alarm_runbook_gaps(tf, runbook) == (3, ["headway-b", "headway-c"])


def test_the_real_alarms_all_link_to_the_runbook() -> None:
    count, bad = r2.alarm_runbook_gaps(r2.read("infra/main/alerts.tf"), r2.read("docs/RUNBOOK.md"))
    assert count > 10 and bad == []


def test_coverage_violation_is_numerator_above_denominator() -> None:
    table = {"coverage_numerator": [284, 5, None], "coverage_denominator": [283, 5, 0]}
    assert r2.coverage_violations(table) == 1
    assert r2.coverage_violations({}) == 0


def test_a_day_is_closed_after_six_local_the_next_day() -> None:
    assert r2.last_closed_day(datetime(2026, 11, 28, 12, 0, tzinfo=UTC)) == date(2026, 11, 27)
    assert r2.last_closed_day(datetime(2026, 11, 28, 10, 0, tzinfo=UTC)) == date(2026, 11, 26)


def test_static_b_checks_hold_on_this_repository() -> None:
    for check in (r2.b1, r2.b3, r2.b5):
        assert check()[0] == "PASS", check.__name__
    assert r2.b2()[0] in {"PASS", "MANUAL"}
