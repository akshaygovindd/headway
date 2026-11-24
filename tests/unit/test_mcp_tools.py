"""The five analyst tools over a small local export: validation, limits, timeout, provenance, audit."""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from headway_mcp import config, runner
from headway_mcp.catalog import METRICS
from headway_mcp.client import InProcessMcp
from headway_mcp.tools import TOOL_NAMES, Tools

DAY = date(2026, 11, 22)


def _w(con: duckdb.DuckDBPyConnection, path: Path, sql: str) -> None:
    con.execute(f"copy ({sql}) to '{path}' (format parquet)")


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> str:
    base = tmp_path_factory.mktemp("export")
    gold = base / "gold"
    gold.mkdir()
    con = duckdb.connect()
    lines = ["Red", "Orange", "Blue"]
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


RANGE = {"start": str(DAY - timedelta(days=2)), "end": str(DAY)}


def test_line_reliability_rows_and_provenance(tools: Tools) -> None:
    res = tools.get_line_reliability("Red", "predicted_deviation_median_s", "last_24h")
    assert res["status"] == "ok"
    assert res["rows"][0]["predicted_deviation_median_s"] == 60.0
    prov = res["provenance"]
    assert prov["tables"] == ["agg_line_reliability_window"]
    assert prov["metrics_md"].startswith("docs/METRICS.md#1-")
    assert prov["comparison_period"]["window"] == "last_24h"
    assert prov["coverage_pct"] == 90.0
    assert prov["freshness"]["exported_at"] == "2026-11-23T00:00:00Z"
    assert prov["sql"].startswith("select") and "'Red'" in prov["sql"]


def test_low_coverage_value_is_withheld(tools: Tools) -> None:
    res = tools.get_line_reliability("Blue", "predicted_deviation_median_s", "last_24h")
    assert res["rows"][0]["predicted_deviation_median_s"] is None
    assert res["rows"][0]["coverage_pct"] == 30.0


def test_compare_lines_has_delta_against_the_first(tools: Tools) -> None:
    res = tools.compare_lines(["Red", "Orange"], "predicted_deviation_median_s", "last_24h")
    assert [r["line"] for r in res["rows"]] == ["Red", "Orange"]
    assert res["rows"][1]["delta_predicted_deviation_median_s"] == 140.0


def test_trends_hourly_withhold_and_delta(tools: Tools) -> None:
    res = tools.get_delay_trends("Red", "predicted_deviation_median_s", "hourly", RANGE)
    rows = res["rows"]
    assert len(rows) == 6
    assert all(r["predicted_deviation_median_s"] is None for r in rows if r["hour"] == "09:00")
    assert res["provenance"]["comparison_period"]["end_date"] == str(DAY)


def test_alerts_and_health(tools: Tools) -> None:
    alerts = tools.get_service_alerts("Red", RANGE)
    assert alerts["rows"][0]["effect"] == "DETOUR"
    health = tools.get_pipeline_health(RANGE)
    assert health["rows"][0]["quarantined_records"] == 3


@pytest.mark.parametrize(
    "call",
    [
        lambda t: t.get_line_reliability("Purple", "coverage_pct", "last_24h"),
        lambda t: t.get_line_reliability("Red", "vibes", "last_24h"),
        lambda t: t.get_line_reliability("Red", "coverage_pct", "last_year"),
        lambda t: t.compare_lines(["Red"], "coverage_pct", "last_24h"),
        lambda t: t.compare_lines(["Red", "Red"], "coverage_pct", "last_24h"),
        lambda t: t.get_delay_trends("Red", "coverage_pct", "weekly", RANGE),
        lambda t: t.get_delay_trends("Red'; drop", "coverage_pct", "daily", RANGE),
        lambda t: t.get_delay_trends("Red", "coverage_pct", "hourly", {"start": "x", "end": "y"}),
        lambda t: t.get_delay_trends(
            "Red", "coverage_pct", "hourly", {"start": "2026-11-22", "end": "2026-11-01"}
        ),
    ],
)
def test_enum_and_date_validation(tools: Tools, call: Any) -> None:
    res = call(tools)
    assert res["status"] == "invalid_input"


def test_range_limits(tools: Tools) -> None:
    long31 = {"start": "2026-10-23", "end": "2026-11-22"}  # 31 days: allowed
    too_long = {"start": "2026-10-22", "end": "2026-11-22"}  # 32 days
    assert tools.get_delay_trends("Red", "coverage_pct", "hourly", long31)["status"] == "ok"
    assert tools.get_delay_trends("Red", "coverage_pct", "hourly", too_long)["status"] == (
        "invalid_input"
    )
    seven = {"start": "2026-11-16", "end": "2026-11-22"}
    eight = {"start": "2026-11-15", "end": "2026-11-22"}
    assert tools.get_service_alerts("Red", seven)["status"] == "ok"
    assert tools.get_service_alerts("Red", eight)["status"] == "invalid_input"
    assert tools.get_pipeline_health(eight)["status"] == "invalid_input"


def test_metric_not_in_the_export_is_not_available(tools: Tools) -> None:
    res = tools.get_line_reliability("Red", "on_time_pct", "last_24h")
    assert res["status"] == "not_available"
    assert "predicted_deviation_median_s" in res["available_metrics"]
    assert "on_time_pct" not in res["available_metrics"]  # its table is not in this export


def test_row_limit(tools: Tools, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_ROWS", 2)
    res = tools.get_delay_trends("Red", "predicted_deviation_median_s", "hourly", RANGE)
    assert len(res["rows"]) == 2 and res["provenance"]["truncated"] is True


def test_timeout(tools: Tools, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "QUERY_TIMEOUT_S", 0.2)
    slow = "select count(*) from range(10000000000) a, range(1000) b"
    with pytest.raises(runner.ToolTimeoutError):
        runner.run(tools.base, slow)


def test_audit_line_per_call(tools: Tools, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="headway_mcp.audit"):
        tools.get_line_reliability("Red", "coverage_pct", "last_24h")
        tools.get_line_reliability("Nope", "coverage_pct", "last_24h")
    lines = [json.loads(r.message) for r in caplog.records if r.name == "headway_mcp.audit"]
    assert [x["status"] for x in lines] == ["ok", "invalid_input"]
    assert set(lines[0]) == {"question_id", "tool", "args", "rows", "latency_ms", "status"}
    assert lines[0]["tool"] == "get_line_reliability" and lines[0]["rows"] == 1


def test_only_five_tools_over_mcp(tools: Tools) -> None:
    mcp = InProcessMcp(tools)
    assert tuple(mcp.list_tools()) == TOOL_NAMES
    res = mcp.call("get_line_reliability",
                   {"line": "Red", "metric": "coverage_pct", "window": "last_24h"})  # fmt: skip
    assert res["status"] == "ok" and res["rows"][0]["coverage_pct"] == 90.0


def test_catalog_metrics_have_units_in_their_names() -> None:
    for name in METRICS:
        assert name.endswith(("_s", "_pct")), name
