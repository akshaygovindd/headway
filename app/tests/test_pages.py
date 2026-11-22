"""Smoke tests: every page renders against an export built from the sample."""

import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1]
PAGES = {
    "Overview": "Home.py",
    "Live map": "pages/1_Live_map.py",
    "Line explorer": "pages/2_Line_explorer.py",
    "Pipeline health": "pages/3_Pipeline_health.py",
}
HEADER = "Headway"
SUBTITLE = "Near-Real-Time Transit Reliability & AI Analytics"
TAGLINE = "Know the gap. Measure the reliability."
NOTE = "Headway approximations, not official MBTA metrics."


@pytest.fixture
def data_url(export_root: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"file://{export_root}"
    monkeypatch.setenv("HEADWAY_DATA_URL", url)
    return url


def _text(at: AppTest) -> str:
    parts = [e.value for e in (*at.title, *at.header, *at.subheader, *at.markdown, *at.caption)]
    return "\n".join(str(p) for p in parts)


@pytest.mark.parametrize(("name", "script"), PAGES.items())
def test_page_renders_with_chrome(name: str, script: str, data_url: str) -> None:
    at = AppTest.from_file(str(APP / script), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    text = _text(at)
    assert HEADER in [t.value for t in at.title]
    assert SUBTITLE in [t.value for t in at.subheader]
    assert TAGLINE in text and NOTE in text
    assert "Metrics as of" in text and "Feed freshness" in text
    assert name in [h.value for h in at.header]


def test_overview_has_a_card_with_coverage_per_line(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()
    captions = [c.value for c in at.caption]
    cards = [c for c in captions if re.match(r"Coverage (\d|n/a)", c)]
    assert cards and all("exported" in c for c in cards)


def test_live_map_renders_banner_for_an_old_snapshot(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/1_Live_map.py"), default_timeout=60).run()
    assert any("old" in w.value for w in at.warning)  # the sample snapshot is not recent


def test_line_explorer_filters(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/2_Line_explorer.py"), default_timeout=60).run()
    assert not at.exception
    assert [s.label for s in at.selectbox] == ["Line", "Direction"]
    at.selectbox[1].select(0).run()
    assert not at.exception
    assert "Slowest stretches" in _text(at)


def test_pipeline_health_cost_panel_has_no_numbers(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/3_Pipeline_health.py"), default_timeout=60).run()
    assert any("available after Phase 5b" in i.value for i in at.info)


def test_missing_data_url_stops_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEADWAY_DATA_URL", raising=False)
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()
    assert not at.exception
    assert any("not configured" in e.value for e in at.error)
