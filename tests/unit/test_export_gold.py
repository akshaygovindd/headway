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
    "dim_route",
    "dim_stop",
    "route_shapes",
}


@pytest.fixture(scope="module")
def gold(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run dbt")
    spec = importlib.util.spec_from_file_location("build_demo", ROOT / "scripts" / "build_demo.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    public: Path = module.build(tmp_path_factory.mktemp("demo"), "samples/boston", "boston")
    return public / "gold"


def test_only_allowlisted_files_are_written(gold: Path) -> None:
    names = {p.name for p in gold.rglob("*")}
    assert names == {f"{t}.parquet" for t in ALLOWLIST} | {"manifest.json"}


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
