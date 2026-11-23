"""Smoke tests: every page renders against an export built from the sample."""

import re
from datetime import date
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


def test_about_page_links_the_docs(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/4_About.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert "About & methodology" in [h.value for h in at.header]
    text = "\n".join(str(m.value) for m in at.markdown)
    for doc in ("METRICS.md", "INCIDENTS.md", "MEASUREMENTS.md"):
        assert doc in text


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


def test_pipeline_health_cost_panel_without_data(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/3_Pipeline_health.py"), default_timeout=60).run()
    assert not at.exception
    assert any("No cost data yet" in i.value for i in at.info)


def test_pipeline_health_cost_panel_with_data(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    import pandas as pd

    root = export_root.parent / "with_cost"
    shutil.copytree(export_root, root, dirs_exist_ok=True)
    pd.DataFrame(
        {
            "date": [date(2026, 11, 21)] * 2,
            "service": ["AWS Lambda", "Amazon S3"],
            "cost_usd": [0.30, 0.10],
            "day_total_usd": [0.40, 0.40],
            "captures": [4000, 4000],
            "cost_per_1000_captures": [0.10, 0.10],
        }
    ).to_parquet(root / "gold" / "cost_daily.parquet")
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(str(APP / "pages/3_Pipeline_health.py"), default_timeout=60).run()
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Spend on 2026-11-21"] == "$0.40"
    assert metrics["Per 1,000 captures"] == "$0.1000"
    assert not any("No cost data yet" in i.value for i in at.info)


def test_invalid_data_url_stops_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_DATA_URL", "ftp://example.org/boston")
    at = AppTest.from_file(str(APP / "Home.py"), default_timeout=60).run()
    assert not at.exception
    assert any("not configured" in e.value for e in at.error)
