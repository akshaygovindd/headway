"""export_gold writes exactly the allowlisted tables and a consistent manifest."""

import importlib.util
import json
import shutil
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST = {
    "agg_line_reliability_hourly",
    "agg_line_reliability_window",
    "agg_stop_deviation_daily",
    "agg_pipeline_health",
    "agg_quality_checks_hourly",
    "agg_quarantine_reasons",
    "dim_route",
    "dim_stop",
    "route_shapes",
}


R2_ALLOWLIST = {
    "agg_arrival_reconstruction_daily",
    "agg_prediction_accuracy_daily",
    "agg_actual_deviation_hourly",
    "agg_headway_adherence_hourly",
    "agg_ett_daily",
    "agg_active_alerts",
    "agg_accuracy_window",
    "agg_actual_deviation_window",
    "agg_headway_window",
    "agg_ett_window",
    "agg_actual_deviation_daily",
    "agg_headway_adherence_daily",
    "agg_slo_daily",
    "agg_slo_rollup",
}


R3_ALLOWLIST = {
    "agg_forecast_next_hour",
    "agg_forecast_gate_status",
    "agg_incidents",
    "agg_alert_matching_daily",
    "agg_weather_comparison",
}


def _build(tmp_path_factory: pytest.TempPathFactory, enable_r2: bool) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run dbt")
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public: Path = module.build(
        tmp_path_factory.mktemp("demo"), "samples/boston", "boston", enable_r2=enable_r2
    )
    return public / "gold"


@pytest.fixture(scope="module")
def gold_r2(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory, True)


@pytest.fixture(scope="module")
def gold(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Export with Release 2 off: the base allowlist only."""
    return _build(tmp_path_factory, False)


def test_only_allowlisted_files_are_written(gold: Path) -> None:
    names = {p.name for p in gold.rglob("*")}
    assert names == {f"{t}.parquet" for t in ALLOWLIST} | {"manifest.json"}


def test_release_2_and_3_tables_are_added_when_enabled(gold_r2: Path) -> None:
    everything = ALLOWLIST | R2_ALLOWLIST | R3_ALLOWLIST
    names = {p.name for p in gold_r2.rglob("*")}
    # analyst_examples.json is written by build_demo (the transform does so on AWS), not by dbt
    extra = {"manifest.json", "analyst_examples.json"}
    assert names == {f"{t}.parquet" for t in everything} | extra
    manifest = json.loads((gold_r2 / "manifest.json").read_text())
    assert set(manifest["row_counts"]) == everything


def test_gate_never_passed_exports_the_gate_status_and_no_backtest(gold_r2: Path) -> None:
    assert not (gold_r2 / "agg_forecast_backtest.parquet").exists()
    gate = _table(gold_r2, "agg_forecast_gate_status")
    assert gate and not any(r["gate_passed"] for r in gate)
    assert {r["source"] for r in gate} == {"transform_build"}
    nxt = _table(gold_r2, "agg_forecast_next_hour")
    assert nxt and {r["forecast_status"] for r in nxt} == {"baselines_only"}
    assert all(r["model_forecast_s"] is None for r in nxt)


def _table(gold: Path, name: str) -> list[dict[str, object]]:
    return pq.read_table(gold / f"{name}.parquet").to_pylist()  # type: ignore[no-any-return]


def test_manifest_matches_the_parquet_files(gold: Path) -> None:
    manifest = json.loads((gold / "manifest.json").read_text())
    assert set(manifest["row_counts"]) == ALLOWLIST
    for table, n in manifest["row_counts"].items():
        assert len(_table(gold, table)) == n, table
    for key in ("exported_at", "dbt_run_at", "metrics_as_of"):
        assert datetime.strptime(manifest[key], "%Y-%m-%dT%H:%M:%SZ")


def test_route_shapes_are_subway_only_with_colours(gold: Path) -> None:
    rows = _table(gold, "route_shapes")
    assert rows
    subway = {"Red", "Orange", "Blue", "Mattapan"} | {f"Green-{x}" for x in "BCDE"}
    assert {r["route_id"] for r in rows} <= subway
    assert all(r["route_color"] for r in rows)


def test_stop_deviation_has_stop_sequence(gold: Path) -> None:
    rows = _table(gold, "agg_stop_deviation_daily")
    assert rows and all(r["stop_sequence"] is not None for r in rows)
