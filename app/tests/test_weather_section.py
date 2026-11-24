"""Weather comparison: wording, withholding under 5 days, never causal."""

import re
from pathlib import Path

import pandas as pd
import pytest
from r3_fixtures import weather_table, with_r3, with_weather
from streamlit.testing.v1 import AppTest
from withheld import rendered_text

from lib import weather

PAGE = str(Path(__file__).resolve().parents[1] / "pages/6_Forecast.py")
CAUSAL = re.compile(
    r"\b(because|due\s+to|caused?|causing|as\s+a\s+result|owing\s+to|effect\s+of|impact)\b", re.I
)


def test_published_sentence_is_descriptive_with_the_row_values() -> None:
    items = weather.statements(weather_table())
    on_time = next(i for i in items if i.condition == "snow" and i.metric == "on_time")
    assert on_time.text == (
        "On snow days, the on-time share was 58.1% vs 80.6% on other days "
        "(9 vs 50 days; 95% intervals 56.7% to 59.4% and 79.7% to 81.4%)."
    )
    assert not on_time.withheld


def test_under_five_days_is_withheld_without_values() -> None:
    items = weather.statements(weather_table())
    rain = next(i for i in items if i.condition == "heavy_rain" and i.metric == "on_time")
    assert rain.withheld
    assert rain.text == (
        "On heavy-rain days, the on-time share is not shown: fewer than 5 heavy-rain days "
        "(3 heavy-rain days and 56 other days in the window)."
    )
    assert not re.search(r"\d+\.\d%", rain.text)
    nothing = weather.statements(weather_table(published=False))
    assert all(i.withheld for i in nothing)


def test_no_causal_wording_anywhere() -> None:
    texts = [i.text for i in weather.statements(weather_table())] + [weather.NOTE]
    for text in texts:
        assert not CAUSAL.search(text), text


def test_statements_order_and_window() -> None:
    items = weather.statements(weather_table().sample(frac=1, random_state=1))
    assert [i.condition for i in items][:2] == ["snow", "snow"]
    assert weather.window_text(weather_table()) == "Window: 2026-01-01 to 2026-03-01."
    empty = weather_table().assign(window_start=pd.NaT, window_end=pd.NaT)
    assert weather.window_text(empty) is None


def test_forecast_page_shows_the_section(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = with_weather(with_r3(export_root, tmp_path / "r3", None), tmp_path / "wx")
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    text = rendered_text(at)
    assert "Weather comparison" in [s.value for s in at.subheader]
    assert "On snow days, the on-time share was 58.1% vs 80.6% on other days" in text
    assert "is not shown: fewer than 5 heavy-rain days" in text
    assert "says nothing about why they differ" in text
    assert not CAUSAL.search(text.split("Weather comparison")[-1])


def test_forecast_page_without_the_table_has_no_section(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = with_r3(export_root, tmp_path / "r3", None)
    (root / "gold" / "agg_weather_comparison.parquet").unlink(missing_ok=True)
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception
    assert "Weather comparison" not in [s.value for s in at.subheader]


def test_a_table_with_no_weather_days_says_there_is_no_data_yet(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = weather_table().assign(n_days_condition=0, n_days_other=0, withheld=True)
    assert weather.has_no_days(empty) and not weather.has_no_days(weather_table())
    root = with_r3(export_root, tmp_path / "r3", None)
    empty.to_parquet(root / "gold" / "agg_weather_comparison.parquet")
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception
    text = rendered_text(at)
    assert "No daily weather in this export yet" in text
    assert "is not shown" not in text
