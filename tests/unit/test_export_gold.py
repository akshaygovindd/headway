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
    "agg_countdown_accuracy_window",
    "agg_countdown_accuracy_daily",
}


R3_ALLOWLIST = {
    "agg_forecast_next_hour",
    "agg_forecast_gate_status",
    "agg_incidents",
    "agg_alert_matching_daily",
    "agg_weather_comparison",
}


R4_ALLOWLIST = {
    "agg_headway_segment_window",
    "agg_marey_last_hour",
    "agg_capture_heartbeat_hourly",
    "agg_ett_band_window",
    "agg_service_disruptions",
}


def _build(
    tmp_path_factory: pytest.TempPathFactory, enable_r2: bool, enable_r4: bool = True
) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run dbt")
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public: Path = module.build(
        tmp_path_factory.mktemp("demo"),
        "samples/boston",
        "boston",
        enable_r2=enable_r2,
        enable_r4=enable_r4,
    )
    return public / "gold"


@pytest.fixture(scope="module")
def gold_r2(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory, True)


@pytest.fixture(scope="module")
def gold_r2_no_r4(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Releases 2 and 3 on, Release 4 off: production before the R4 switch."""
    return _build(tmp_path_factory, True, enable_r4=False)


@pytest.fixture(scope="module")
def gold(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Export with Release 2 off: the base allowlist only."""
    return _build(tmp_path_factory, False)


def test_only_allowlisted_files_are_written(gold: Path) -> None:
    names = {p.name for p in gold.rglob("*")}
    assert names == {f"{t}.parquet" for t in ALLOWLIST} | {"manifest.json"}


def test_release_4_tables_are_absent_when_r4_is_off(gold_r2_no_r4: Path) -> None:
    names = {p.name for p in gold_r2_no_r4.rglob("*")}
    assert names == {f"{t}.parquet" for t in ALLOWLIST | R2_ALLOWLIST | R3_ALLOWLIST} | {
        "manifest.json", "analyst_examples.json"}  # fmt: skip


def test_release_4_site_tables_have_rows_and_columns(gold_r2: Path) -> None:
    seg = _table(gold_r2, "agg_headway_segment_window")
    assert seg and {r["window_label"] for r in seg} == {"last_24h", "last_7d"}
    assert {"from_stop_name", "segment_order", "health", "disrupted", "coverage_pct"} <= set(seg[0])
    marey = _table(gold_r2, "agg_marey_last_hour")
    assert marey and {r["method"] for r in marey} <= {"observed", "estimated"}
    beat = _table(gold_r2, "agg_capture_heartbeat_hourly")
    assert beat and {"expected_runs", "missed_runs", "feed_gap_min", "data_outage"} <= set(beat[0])
    ett_cols = set(pq.read_schema(gold_r2 / "agg_ett_band_window.parquet").names)
    assert {
        "direction_id",
        "time_band",
        "n_trips",
        "median_scheduled_s",
        "coverage_pct",
    } <= ett_cols
    dis_cols = set(pq.read_schema(gold_r2 / "agg_service_disruptions.parquet").names)
    assert {"alert_id", "header_text", "is_active", "segment_keys"} <= dis_cols


def test_release_2_3_and_4_tables_are_added_when_enabled(gold_r2: Path) -> None:
    everything = ALLOWLIST | R2_ALLOWLIST | R3_ALLOWLIST | R4_ALLOWLIST
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


def test_manifest_records_the_real_parquet_column_types(gold_r2: Path) -> None:
    manifest = json.loads((gold_r2 / "manifest.json").read_text())
    types = manifest["column_types"]
    assert set(types) == set(manifest["row_counts"])
    for table, columns in types.items():
        schema = pq.read_schema(gold_r2 / f"{table}.parquet")
        assert list(columns) == schema.names, table  # same columns, same order
    # spot checks: types the naming convention used to guess wrong or leave unclassified
    assert types["agg_headway_segment_window"]["coverage_pct"] == "DOUBLE"
    assert types["agg_service_disruptions"]["segment_keys"] == "VARCHAR[]"


def test_committed_gold_column_types_match_the_export(gold_r2: Path) -> None:
    """web/src/data/gold-column-types.json (read by the data contracts) has not drifted.

    Regenerate with `uv run python scripts/snapshot_gold_types.py`, then `npm run contracts`.
    """
    committed = json.loads((ROOT / "web/src/data/gold-column-types.json").read_text())
    exported = json.loads((gold_r2 / "manifest.json").read_text())["column_types"]
    assert committed == exported
