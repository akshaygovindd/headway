"""The Forecast page in its three states: no R3 files, gate not passed, gate passed."""

from pathlib import Path

import pytest
from r3_fixtures import with_r3
from streamlit.testing.v1 import AppTest
from withheld import assert_withheld, rendered_text

APP = Path(__file__).resolve().parents[1]
PAGE = str(APP / "pages/6_Forecast.py")


def _run(root: Path, monkeypatch: pytest.MonkeyPatch) -> AppTest:
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{root}")
    at = AppTest.from_file(PAGE, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_without_r3_files_the_page_says_so(
    export_root_no_r2: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run(export_root_no_r2, monkeypatch)
    assert "Forecast" in [h.value for h in at.header]
    assert any("not enabled" in i.value for i in at.info)
    assert not at.dataframe


def test_real_export_gate_not_passed_shows_baselines_and_the_unlock_rule(
    export_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    at = _run(export_root, monkeypatch)
    text = rendered_text(at)
    assert "comparison unlocks after 21 service days incl. 3 weekends; currently 0 days" in text
    assert "baselines only" in text and "MBTA prediction (reference, not a baseline)" in text
    assert "Model (LightGBM)" not in text and "model better" not in text
    assert not any("Backtest" in m.value for m in at.markdown)
    assert_withheld(at)


@pytest.mark.parametrize(
    ("state", "banner"),
    [("win", "model better by 30 s"), ("lose", "model worse by 40 s"),
     ("tie", "no significant difference")],
)  # fmt: skip
def test_gate_passed_shows_the_honest_comparison(
    state: str, banner: str, export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = with_r3(export_root, tmp_path / "root", state)
    at = _run(root, monkeypatch)
    text = rendered_text(at) + "\n".join(
        str(e.value) for e in (*at.success, *at.error, *at.warning)
    )
    assert banner in text
    assert "Typical for day type and hour (baseline)" in text  # the best baseline is named
    assert "Model (LightGBM)" in text and "MBTA prediction (reference, not a baseline)" in text
    assert_withheld(at)
    for line, passed in (("Red", True), ("Mattapan", False)):
        assert line in [s.value for s in at.subheader]
        assert passed or "No model comparison is shown for this line" in text
    # a line below the gate gets no comparison banner at all
    assert text.count(banner) == 2 * 3  # Red and Blue, three horizons each


def test_low_coverage_values_are_withheld_on_the_page(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = with_r3(export_root, tmp_path / "root", "win")
    at = _run(root, monkeypatch)
    blue = [d.value for d in at.dataframe if "Forecast delay" in d.value.columns]
    withheld = [
        r for f in blue for _, r in f.iterrows()
        if r["Method"] == "Persistence (baseline)" and r["Coverage"] == "25%"
    ]  # fmt: skip
    assert withheld and all(r["Forecast delay"].startswith("withheld") for r in withheld)


def test_no_backtest_file_means_no_comparison_even_if_the_gate_says_passed(
    export_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = with_r3(export_root, tmp_path / "root", None)
    at = _run(root, monkeypatch)
    assert "model better" not in rendered_text(at)
    assert not any(x.value.startswith("15 min ahead") for x in (*at.success, *at.error, *at.info))
