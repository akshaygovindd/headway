"""The blocking number and causal check of incident summaries (ADR-008)."""

import json
from typing import Any

import pytest

from headway_transform import incident_numcheck as nc
from headway_transform import incident_summaries as isum

HEADER = "Shuttle buses replace trains between Park St and Kenmore because of a track problem"


def evidence(**over: Any) -> dict[str, Any]:
    doc = isum._smoke_evidence("Red", "15:00", "16:00")
    doc["alerts"] = [{"alert_id": "a1", "effect": "DETOUR", "header": HEADER + " until 6:30 PM"}]
    doc.update(over)
    return doc


def ok(text: str, ev: dict[str, Any] | None = None) -> bool:
    return nc.check(text, ev or evidence()).ok


@pytest.mark.parametrize(
    "text",
    [
        "Red (direction 0) was late for 2 hours.",  # integer, count
        "Median deviation reached 420 s.",  # seconds
        "The gap share reached 35%.",  # percent
        "The gap share reached 35 percent.",
        "Coverage was 88.0% at its lowest.",  # decimal with a unit
        "Between 15:00 and 17:00 service was disrupted.",  # clock times
        "From 3 PM to 5 p.m. service was disrupted.",  # 12-hour clock
        "The date was 2026-11-22.",  # ISO date from the evidence
        "That is 380 s above the baseline of 40 s.",
        "The threshold of 180 s was crossed during two hours.",
    ],
)
def test_numbers_that_match_the_evidence_pass(text: str) -> None:
    assert ok(text), nc.check(text, evidence()).problems


@pytest.mark.parametrize(
    "text",
    [
        "Delays reached 99999 s.",  # integer not in evidence
        "The gap share reached 36%.",  # percent off by one
        "Coverage was 87.5% at its lowest.",  # decimal off
        "Service ran from 15:30 to 17:00.",  # clock not in evidence
        "From 6 PM service was disrupted.",  # 12-hour clock not in evidence
        "On 2026-11-23 service was disrupted.",  # date not in evidence
        "The incident lasted 3 hours.",  # duration, wrong count
        "It lasted five hours.",  # spelled-out number not in evidence
        "It happened on the 7th.",  # ordinal
        "About 9 minutes late.",  # 540 s is not an evidence value
    ],
)
def test_numbers_that_do_not_match_are_rejected(text: str) -> None:
    result = nc.check(text, evidence())
    assert not result.ok and result.problems


def test_display_rounding_either_way() -> None:
    ev = evidence()  # 420.0 s, 380.0 s, 35.0 %
    assert ok("The median reached 420 s.", ev)
    ev_dec = isum._smoke_evidence("Red", "15:00", "16:00")
    ev_dec["hours"][0]["median_deviation_s"] = 419.6
    assert nc.check("The median reached 420 s.", ev_dec).ok  # rounded to an integer
    assert nc.check("The median reached 419.6 s.", ev_dec).ok  # shown exactly
    assert not nc.check("The median reached 419.0 s.", ev_dec).ok  # one decimal: 419.6 is not 419.0
    assert nc.check("Coverage was 88% at its lowest.", ev_dec).ok  # 88.0 shown as an integer


def test_unit_conversions_seconds_minutes_hours() -> None:
    ev = isum._smoke_evidence("Red", "15:00", "16:00")
    ev["hours"][0]["median_deviation_s"] = 420.0
    assert nc.check("Deviation reached 7 min.", ev).ok  # 420 s = 7 min
    assert nc.check("Deviation reached 7.0 minutes.", ev).ok
    assert not nc.check("Deviation reached 8 min.", ev).ok
    ev["hours"][0]["median_deviation_s"] = 210.0
    assert nc.check("Deviation reached 3.5 min.", ev).ok
    assert nc.check("Deviation reached 4 min.", ev).ok  # 3.5 shown rounded to a whole minute
    assert not nc.check("Deviation reached 9 min.", ev).ok
    ev["hours"][0]["median_deviation_s"] = 7200.0
    assert nc.check("Deviation reached 2 hours.", ev).ok  # s -> h
    # a unit is not interchangeable: 35 is a percent, not seconds
    assert not nc.check("Deviation reached 35 s.", evidence()).ok
    # nor is a count a duration: n_hours is 2, but 2 minutes is not 2 hours
    assert not nc.check("It lasted 2 minutes.", evidence()).ok


def test_numbers_in_a_quoted_alert_header_are_evidence() -> None:
    text = f'A service alert was active: "{HEADER} until 6:30 PM".'
    assert nc.check(text, evidence()).ok  # 6:30 PM is a clock in the header (and only there)
    assert not ok("Trains return at 7:30 PM.")


def test_causal_words_only_inside_a_quoted_alert_header() -> None:
    assert not ok("Trains were late because a train broke down.")
    assert not ok("The delay was due to signal problems.")
    assert not ok("The delay was caused by a track problem.")
    assert not ok("As a result of the delay, service gapped.")
    assert not ok("Due to this, service gapped.")
    quoted = f'The alert said: "{HEADER}".'
    assert ok(quoted)  # "because" inside a verbatim header quote
    # a quote that is not an alert header gives no cover
    assert not ok('Someone said: "because it rained".')
    # causal wording right outside the quote is still rejected
    assert not ok(f'Service was late because "{HEADER}".')
    # curly quotes work too
    assert ok(f"The alert said: “{HEADER}”.")


def test_template_passes_its_own_check_for_every_rule() -> None:
    ev = evidence(is_open=True)
    ev["hours"][0]["crossed"] = ["deviation", "gap", "skipped", "alert"]
    ev["hours"][0]["skipped_share_pct"] = 25.0
    ev["data_outage_hours_inside"] = 1
    ev["baseline_rule"] = "disabled in some hours: fewer than 14 earlier service days"
    ev["min_coverage_pct"] = 41.3
    text = isum.template_summary(ev)
    assert "less certain" in text and "still open" in text
    assert nc.check(text, ev).ok, (text, nc.check(text, ev).problems)
    # and the evidence JSON string form
    assert nc.check(text, json.dumps(ev)).ok


def test_extract_forms() -> None:
    kinds = [
        (t.kind, t.value, t.unit) for t in nc.extract("35% at 14:05, 2 PM, 3 min, 210 s, 2 hours")
    ]
    assert kinds == [
        ("number", 35.0, "pct"), ("clock", 14 * 60 + 5, None), ("clock", 14 * 60, None),
        ("number", 3.0, "min"), ("number", 210.0, "s"), ("number", 2.0, "h"),
    ]  # fmt: skip
    assert [t.value for t in nc.extract("Line Green-B had 1,200 riders")] == [1200.0]


def test_direction_agreeing_with_the_evidence_passes() -> None:
    # the evidence has deviation 380 s above its baseline (a positive change, higher is worse)
    for text in (
        "Delays rose to 420 s.",
        "Deviation was higher than usual, 380 s above the baseline of 40 s.",
        "Service worsened and was slower.",
    ):
        assert ok(text), nc.check(text, evidence()).problems


@pytest.mark.parametrize(
    "text",
    [
        "Service improved to 420 s.",
        "Delays fell.",
        "Trains were faster than usual.",
        "The deviation decreased.",
        "Things got better.",
    ],
)
def test_direction_against_the_evidence_is_rejected(text: str) -> None:
    result = nc.check(text, evidence())
    assert not result.ok
    assert any("direction" in p for p in result.problems)


def test_direction_without_a_change_in_the_evidence_is_rejected() -> None:
    doc = {"line": "Red", "median": 12.0, "hours": [{"hour": "15:00", "median_deviation_s": 12.0}]}
    for text in ("Delays rose.", "It improved.", "It was lower."):
        assert not nc.check(text, doc).ok
    assert nc.check("The median was 12 s.", doc).ok  # no direction word, nothing to check


def test_direction_of_a_current_against_previous_change() -> None:
    doc = {"on_time_change": -4.0, "on_time_pct": 71.0}  # on time fell by 4 points
    assert nc.check("On-time share fell to 71%.", doc).ok
    assert nc.check("The on-time share worsened.", doc).ok  # higher is better here
    assert not nc.check("On-time share improved.", doc).ok
    assert not nc.check("On-time share rose.", doc).ok
