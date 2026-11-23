"""Policy tests: card renderers require coverage and freshness (A6); no feed URLs in app/ (A7)."""

import inspect
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from withheld import assert_withheld, rendered_text, table_violations

import ui
from lib.coverage import WITHHELD_TEXT, is_withheld

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parent


def test_metric_card_requires_coverage_and_freshness() -> None:
    params = inspect.signature(ui.render_metric_card).parameters
    for name in ("coverage_pct", "freshness"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert params[name].default is inspect.Parameter.empty
    with pytest.raises(TypeError):
        ui.render_metric_card("Red", {"x": 1.0})  # type: ignore[call-arg]


def test_no_mbta_feed_url_in_app() -> None:
    config_urls = set(re.findall(r"https?://\S+", (ROOT / "config/cities/boston.yaml").read_text()))
    assert config_urls, "expected feed URLs in the city config"
    pattern = re.compile(r"mbta\.com|MBTA_GTFS|\.pb\b|TripUpdates|VehiclePositions", re.IGNORECASE)
    offenders = []
    for path in APP.rglob("*"):
        if (
            not path.is_file()
            or path.suffix not in {".py", ".txt", ".toml", ".md", ".yaml", ".yml"}
            or path.name == "test_policies.py"
            or "__pycache__" in path.parts
        ):
            continue
        text = path.read_text()
        offenders += [str(path) for url in config_urls if url in text]
        offenders += [str(path) for m in pattern.finditer(text) if "MBTA metrics" not in text]
    assert not offenders, offenders


def _gold(tmp_path: Path, cov: dict[str, float] | None = None) -> Path:
    """A tiny Release 2 export: Red has 35% coverage and recognisable values, Blue 90%."""
    base = tmp_path / "public"
    (base / "gold").mkdir(parents=True)
    (base / "live").mkdir()
    (base / "gold" / "manifest.json").write_text(
        json.dumps({"exported_at": "2026-11-23T20:00:00Z", "metrics_as_of": "2026-11-23T20:00:00Z"})
    )
    (base / "live" / "vehicles_latest.json").write_text(
        json.dumps({"generated_at": "2026-11-23T20:00:00Z",
                    "feed_header_ts": "2026-11-23T20:00:00Z",
                    "vehicle_count": 0, "dropped_invalid": 0, "vehicles": []})
    )  # fmt: skip
    cov = cov or {"Red": 35.0, "Blue": 90.0}
    mark = {"Red": 7771.0, "Blue": 21.0, "Orange": 22.0}  # 7771 must never appear: Red is below 50%
    acc_win = [
        {"line": ln, "window_label": w, "horizon_bucket": b, "n": 50,
         "median_abs_error_s": mark[ln], "mae_s": mark[ln], "pct_within_60s": 61.0,
         "pct_within_120s": 91.0, "n_disruption": 0, "coverage_pct": cov[ln]}
        for ln in cov for w in ("last_24h", "last_7d") for b in ("0-5", "5-10")
    ]  # fmt: skip
    dev_win = [
        {"line": ln, "window_label": w, "n_arrivals": 50, "median_deviation_s": mark[ln] + 1,
         "p90_deviation_s": mark[ln] + 2, "pct_on_time_window": 55.0, "n_excluded_outage": 0,
         "n_disruption": 0, "coverage_pct": cov[ln]}
        for ln in cov for w in ("last_24h", "last_7d")
    ]  # fmt: skip
    acc_daily = [
        {"line": ln, "service_date": date(2026, 11, 23), "horizon_bucket": "0-5",
         "service_disruption": False, "n": 50, "n_excluded_outage": 0,
         "median_abs_error_s": mark[ln], "mae_s": mark[ln], "pct_within_60s": 61.0,
         "pct_within_120s": 91.0, "coverage_numerator": 50,
         "coverage_denominator": 50 / cov[ln] * 100}
        for ln in cov
    ]  # fmt: skip
    tables = {
        "agg_accuracy_window": acc_win,
        "agg_actual_deviation_window": dev_win,
        "agg_prediction_accuracy_daily": acc_daily,
        "agg_actual_deviation_hourly": [
            {"line": ln, "data_outage": False, "service_disruption": False, "n_arrivals": 50,
             "n_excluded_outage": 0, "median_deviation_s": mark[ln] + 1,
             "p90_deviation_s": mark[ln] + 2, "pct_on_time_window": 55.0,
             "coverage_numerator": 50, "coverage_denominator": 50 / cov[ln] * 100}
            for ln in cov
        ],
        "agg_arrival_reconstruction_daily": [
            {"line": ln, "trip_stops": 10, "n_high": 5, "n_medium": 4} for ln in cov
        ],
        "agg_active_alerts": [
            {"line": "Red", "service_date": date(2026, 11, 23), "hour_local": 14,
             "effect": "DETOUR", "is_service_disruption": True}
        ],
    }  # fmt: skip
    for name, rows in tables.items():
        pd.DataFrame(rows).to_parquet(base / "gold" / f"{name}.parquet")
    return base


def test_value_below_half_coverage_is_rendered_nowhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{_gold(tmp_path)}")
    at = AppTest.from_file(str(APP / "pages/5_Accuracy.py"), default_timeout=60).run()
    for window in ("last_24h", "last_7d", "all_days"):
        at.radio[0].set_value(window).run()
        assert_withheld(at)
        text = rendered_text(at)
        assert "7771" not in text and "7772" not in text, window
        assert WITHHELD_TEXT in text
        assert "Service disruption: detour" in text, window
        assert sum(1 for m in at.metric if m.label == "Median deviation") == 1  # Blue only


def test_table_checker_catches_a_visible_value() -> None:
    bad = pd.DataFrame([{"Line": "Red", "Median abs error": "30 s", "Coverage": "35%", "n": 4}])
    ok = bad.assign(**{"Median abs error": WITHHELD_TEXT})
    assert table_violations(bad) and not table_violations(ok)
    assert is_withheld(35.0)


def test_charts_are_not_drawn_from_withheld_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All lines below 50%: no empty figure (Plotly logs y=-Infinity for those), a note instead."""
    monkeypatch.setenv("HEADWAY_DATA_URL", f"file://{_gold(tmp_path, {'Red': 30.0})}")
    at = AppTest.from_file(str(APP / "pages/5_Accuracy.py"), default_timeout=60).run()
    assert_withheld(at)
    assert not at.get("plotly_chart")
    assert any("below 50% coverage" in str(i.value) for i in at.info)
