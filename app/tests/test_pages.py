"""Smoke tests: every page renders against an export built from the sample."""

import re
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1]
PAGES = {
    "Overview": "overview_page.py",
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
    at = AppTest.from_file(str(APP / "overview_page.py"), default_timeout=60).run()
    captions = [c.value for c in at.caption]
    cards = [c for c in captions if re.match(r"Coverage (\d|n/a)", c)]
    assert cards and not any("exported" in c for c in cards)  # freshness is shown once at the top


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
    at = AppTest.from_file(str(APP / "overview_page.py"), default_timeout=60).run()
    assert not at.exception
    assert any("not configured" in e.value for e in at.error)


R2_FILES = (
    "agg_prediction_accuracy_daily",
    "agg_actual_deviation_hourly",
    "agg_headway_adherence_hourly",
    "agg_ett_daily",
    "agg_arrival_reconstruction_daily",
    "agg_accuracy_window",
    "agg_actual_deviation_window",
    "agg_headway_window",
    "agg_ett_window",
    "agg_actual_deviation_daily",
    "agg_headway_adherence_daily",
    "agg_slo_daily",
    "agg_slo_rollup",
)


@pytest.fixture
def data_url_no_r2(export_root_no_r2: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = f"file://{export_root_no_r2}"
    monkeypatch.setenv("HEADWAY_DATA_URL", url)
    return url


def test_export_has_release_2_files_only_when_enabled(
    export_root: Path, export_root_no_r2: Path
) -> None:
    for name in R2_FILES:
        assert (export_root / "gold" / f"{name}.parquet").exists()
        assert not (export_root_no_r2 / "gold" / f"{name}.parquet").exists()
    assert (export_root_no_r2 / "gold" / "agg_line_reliability_hourly.parquet").exists()


def test_accuracy_page_renders_with_release_2(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/5_Accuracy.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Accuracy" in [h.value for h in at.header]
    text = _text(at)
    assert (
        "Prediction accuracy by horizon" in text and "Actual deviation from the timetable" in text
    )
    assert "Reconstruction confidence" in text
    assert not at.info or not any("not enabled" in i.value for i in at.info)
    assert any(c.value.startswith("coverage") or "coverage" in c.value for c in at.caption)


def test_accuracy_page_without_release_2_says_so(data_url_no_r2: str) -> None:
    at = AppTest.from_file(str(APP / "pages/5_Accuracy.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("not enabled" in i.value for i in at.info)
    assert "Prediction accuracy by horizon" not in _text(at)


def test_line_explorer_headway_section_follows_the_flag(
    data_url: str, monkeypatch: pytest.MonkeyPatch, export_root_no_r2: Path
) -> None:
    on = AppTest.from_file(str(APP / "pages/2_Line_explorer.py"), default_timeout=60).run()
    assert not on.exception, [e.value for e in on.exception]
    assert "Headway adherence" in _text(on) and "Slowest stretches" in _text(on)
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{export_root_no_r2}")
    off = AppTest.from_file(str(APP / "pages/2_Line_explorer.py"), default_timeout=60).run()
    assert not off.exception, [e.value for e in off.exception]
    assert "Headway adherence" not in _text(off) and "Slowest stretches" in _text(off)


def test_heavy_rail_shows_headway_before_deviation(data_url: str) -> None:
    at = AppTest.from_file(str(APP / "pages/2_Line_explorer.py"), default_timeout=60).run()
    at.selectbox[0].select("Red").run()
    heads = [s.value for s in at.subheader]
    assert heads.index("Headway adherence") < heads.index("Median deviation by hour and weekday")
    at.selectbox[0].select("Green-B").run()
    heads = [s.value for s in at.subheader]
    assert heads.index("Median deviation by hour and weekday") < heads.index("Headway adherence")


def test_pipeline_health_slo_section_follows_the_flag(
    data_url: str, monkeypatch: pytest.MonkeyPatch, export_root_no_r2: Path
) -> None:
    on = AppTest.from_file(str(APP / "pages/3_Pipeline_health.py"), default_timeout=60).run()
    assert not on.exception, [e.value for e in on.exception]
    assert "Service level objectives" in [s.value for s in on.subheader]
    slo = next(d for d in on.dataframe if "Objective" in d.value.columns)
    assert "Capture completeness (minutes)" in set(slo.value["Objective"])
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{export_root_no_r2}")
    off = AppTest.from_file(str(APP / "pages/3_Pipeline_health.py"), default_timeout=60).run()
    assert not off.exception, [e.value for e in off.exception]
    assert "Service level objectives" not in [s.value for s in off.subheader]


def test_pipeline_health_links_the_observability_report(
    monkeypatch: pytest.MonkeyPatch, export_root: Path
) -> None:
    from lib import data

    assert (
        data.observability_url("https://example.org/public/boston")
        == "https://example.org/public/boston/observability/index.html"
    )
    assert data.observability_url(f"file://{export_root}") is None
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{export_root}")
    at = AppTest.from_file(str(APP / "pages/3_Pipeline_health.py"), default_timeout=60).run()
    assert not at.exception
    assert any(s.value == "Observability report" for s in at.subheader)
    assert any("deployed data only" in c.value for c in at.caption)
