"""Accuracy and the headway view render with real numbers from `make demo-r2` (skipped when the
demo folder has not been built)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from withheld import assert_withheld

APP = Path(__file__).resolve().parents[1]
DEMO = APP.parent / ".local" / "demo-r2" / "public" / "boston"

pytestmark = pytest.mark.skipif(
    not (DEMO / "gold" / "agg_headway_window.parquet").exists(),
    reason="run `make demo-r2` once to build .local/demo-r2",
)


@pytest.fixture(autouse=True)
def _demo_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{DEMO}")


def test_accuracy_and_headway_view_show_real_window_numbers() -> None:
    accuracy = AppTest.from_file(str(APP / "pages/5_Accuracy.py"), default_timeout=120).run()
    assert not accuracy.exception, [e.value for e in accuracy.exception]
    assert accuracy.radio[0].options[:2] == ["Last 24 hours", "Last 7 days"]  # exact windows
    assert accuracy.dataframe and not accuracy.dataframe[0].value.empty
    assert any(m.label == "Median deviation" and m.value != "n/a" for m in accuracy.metric)

    explorer = AppTest.from_file(str(APP / "pages/2_Line_explorer.py"), default_timeout=120).run()
    assert not explorer.exception, [e.value for e in explorer.exception]
    shares = {m.label: m.value for m in explorer.metric}
    assert shares.get("Within 0.5-1.5×", "n/a") != "n/a"
    assert any(r.label == "Window" for r in explorer.radio)


def _numbers(at: AppTest) -> list[object]:
    return [str(df.value.to_dict("list")) for df in at.dataframe] + [m.value for m in at.metric]


def test_last_24_hours_and_last_7_days_differ_on_the_demo_data() -> None:
    at = AppTest.from_file(str(APP / "pages/5_Accuracy.py"), default_timeout=120).run()
    seen = {}
    for window in ("last_24h", "last_7d"):
        at.radio[0].set_value(window).run()
        assert not at.exception, [e.value for e in at.exception]
        seen[window] = _numbers(at)
    assert seen["last_24h"] != seen["last_7d"]


def test_no_value_below_half_coverage_on_the_demo_pages() -> None:
    for page in ("pages/5_Accuracy.py", "pages/2_Line_explorer.py"):
        at = AppTest.from_file(str(APP / page), default_timeout=120).run()
        assert_withheld(at)
        radio = [r for r in at.radio if r.label == "Window"][0]
        for option in ("last_24h", "last_7d", "all_days"):
            radio.set_value(option).run()
            assert_withheld(at)


def test_demo_has_a_red_service_disruption_and_three_windows_in_the_explorer() -> None:
    at = AppTest.from_file(str(APP / "pages/2_Line_explorer.py"), default_timeout=120).run()
    window = [r for r in at.radio if r.label == "Window"][0]
    assert window.options == ["Last 24 hours", "Last 7 days", "All exported days (approximate)"]
    at.selectbox[0].set_value("Red").run()
    assert any("Service disruption" in str(m.value) for m in at.markdown)
