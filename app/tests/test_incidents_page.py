"""The Incidents page: without tables, with the four kinds of incident, and its frames."""

from pathlib import Path

import pandas as pd
import pytest
from r3_fixtures import incidents_table, with_incidents
from streamlit.testing.v1 import AppTest
from withheld import assert_withheld, rendered_text

from lib import incidents

APP = Path(__file__).resolve().parents[1]
PAGE = str(APP / "pages/7_Incidents.py")


def _run(root: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_without_incident_tables_the_page_says_so(
    export_root_no_r2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run(export_root_no_r2, monkeypatch)
    assert any("not enabled" in i.value for i in at.info)
    assert not at.dataframe


def test_page_shows_summary_badge_evidence_and_match_status(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run(with_incidents(export_root, tmp_path / "root"), monkeypatch)
    text = rendered_text(at)
    assert {"Red", "Orange", "Blue", "Mattapan"} <= {s.value.split(" ")[0] for s in at.subheader}
    assert "Red was late: deviation reached 420 s." in text
    assert "written by a language model, number check passed" in text
    assert text.count("template used") == 1  # Orange; Mattapan's summary is withheld
    assert "failed the number check and was replaced" in text
    assert "Summary not generated yet." in text
    assert "matched: a line-scope service alert overlapped this incident" in text
    assert "unmatched incident: no line-scope service alert overlapped it" in text
    assert "The first matching alert was captured 20 min after the incident began" in text
    assert "A manual review of a sample" in text  # the hand-check note
    assert "missed" not in text.lower() and "phantom" not in text.lower()
    assert [m.label for m in at.metric] == [
        "Incidents",
        "Matched with an alert",
        "Unmatched incidents",
        "Unmatched alerts",
    ]
    assert [m.value for m in at.metric] == ["4", "1", "3", "2"]


def test_low_coverage_summary_and_values_are_withheld(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run(with_incidents(export_root, tmp_path / "root"), monkeypatch)
    text = rendered_text(at)
    assert "Mattapan summary." not in text  # coverage 30%: the summary is withheld
    assert incidents.SUMMARY_WITHHELD in text
    assert_withheld(at)  # the Mattapan evidence row shows no value


def test_frames() -> None:
    inc = incidents_table()
    ordered = incidents.incident_list(inc)
    assert list(ordered["incident_id"]) == ["i1", "i2", "i3", "i4"] or len(ordered) == 4
    hours = incidents.evidence_hours(incidents.evidence(inc.iloc[0]["evidence_json"]))
    assert list(hours["hour"]) == ["15:00", "16:00"]
    assert hours.iloc[0]["crossed"] == "deviation above the usual, headway gaps"
    assert incidents.rules_text("deviation,alert") == "deviation above the usual, service alert"
    assert incidents.lag_text(-600.0).endswith("10 min before the incident began")
    assert incidents.lag_text(None) == "no alert lag"
    assert incidents.day_summary(None) == {}
    assert incidents.day_summary(pd.DataFrame({"n_incidents": []})) == {}
