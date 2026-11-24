"""A small local export for the analyst tests."""

import json
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pytest

from headway_mcp.tools import Tools

DAY = date(2026, 11, 22)


def _w(con: duckdb.DuckDBPyConnection, path: Path, sql: str) -> None:
    con.execute(f"copy ({sql}) to '{path}' (format parquet)")


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> str:
    base = tmp_path_factory.mktemp("export")
    gold = base / "gold"
    gold.mkdir()
    con = duckdb.connect()
    rel = " union all ".join(
        f"select '{ln}' line, 'last_24h' window_label, {dev}::double median_predicted_deviation_s,"
        f" 100.0::double p90_predicted_deviation_s, 5.0::double median_abs_revision_s,"
        f" 10 n_eligible, {cov}::double coverage_pct, 0.1::double skipped_share"
        for ln, dev, cov in (("Red", 60, 90), ("Orange", 200, 80), ("Blue", 10, 30))
    )
    _w(con, gold / "agg_line_reliability_window.parquet", rel)
    hours = " union all ".join(
        f"select 'Red' line, 0 direction_id, date '{DAY - timedelta(days=d)}' service_date,"
        f" {h} hour_local, {60 + 30 * d + h}::double median_predicted_deviation_s,"
        f" 100.0::double p90_predicted_deviation_s, 5.0::double median_abs_revision_s,"
        f" 10 n_eligible, {90 if h != 9 else 20}::double coverage_pct, 0.0::double skipped_share"
        for d in range(3)
        for h in (9, 10)
    )
    _w(con, gold / "agg_line_reliability_hourly.parquet", hours)
    _w(
        con,
        gold / "agg_active_alerts.parquet",
        f"select date '{DAY}' service_date, 10 hour_local, 'Red' line, 'DETOUR' effect, true"
        " is_service_disruption, 'line' as scope, 2 n_active_alerts",
    )
    _w(
        con,
        gold / "agg_pipeline_health.parquet",
        f"select timestamptz '{DAY} 10:00:00-05' scheduled_at, 1 vehicle_polls_ok,"
        " 0 vehicle_polls_error, 0 duplicate_delivery_count, 3 quarantine_total,"
        " 'ok' trip_updates_status",
    )
    manifest = {
        "exported_at": "2026-11-23T00:00:00Z",
        "metrics_as_of": f"{DAY}T15:00:00Z",
        "row_counts": {
            "agg_line_reliability_window": 3,
            "agg_line_reliability_hourly": 6,
            "agg_active_alerts": 1,
            "agg_pipeline_health": 1,
        },
    }
    (gold / "manifest.json").write_text(json.dumps(manifest))
    return f"file://{base}"


@pytest.fixture
def tools(root: str) -> Tools:
    return Tools(root)
