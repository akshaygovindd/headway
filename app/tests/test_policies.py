"""Policy tests: card renderers require coverage and freshness (A6); no feed URLs in app/ (A7)."""

import inspect
import re
from pathlib import Path

import pytest

import ui

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
