"""Shared fixtures: small synthetic gold tables and a full export built from the sample."""

import importlib.util
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]


def hourly_row(line: str, hour: int, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "line": line,
        "direction_id": 0,
        "service_date": date(2026, 11, 22),
        "hour_local": hour,
        "median_predicted_deviation_s": 60.0,
        "p90_predicted_deviation_s": 120.0,
        "median_abs_revision_s": 10.0,
        "n_eligible": 10,
        "coverage_numerator": 9,
        "observable_trip_stops": 10,
        "skipped_stop_count": 1,
        "window_complete": True,
    }
    row.update(over)
    return row


@pytest.fixture
def hourly() -> pd.DataFrame:
    return pd.DataFrame(
        [
            hourly_row("Red", 9),
            hourly_row("Red", 10, median_predicted_deviation_s=120.0, n_eligible=30),
            hourly_row("Orange", 9, coverage_numerator=4),
        ]
    )


@pytest.fixture(scope="session")
def export_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """HEADWAY_DATA_URL root built from samples/boston (replay, dbt, export, snapshot)."""
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public: Path = module.build(
        tmp_path_factory.mktemp("export"), "samples/boston", "boston", enable_r2=True
    )
    return public


@pytest.fixture(scope="session")
def export_root_no_r2(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Same export built with HEADWAY_ENABLE_R2=false: no Release 2 tables."""
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public: Path = module.build(
        tmp_path_factory.mktemp("export_no_r2"), "samples/boston", "boston", enable_r2=False
    )
    return public
