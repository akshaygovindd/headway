from datetime import date
from pathlib import Path

import pandas as pd

from lib import data, explorer, health, livemap, overview


def _window_row(line: str, **over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "line": line,
        "window_label": "last_24h",
        "median_predicted_deviation_s": 105.0,
        "p90_predicted_deviation_s": 200.0,
        "median_abs_revision_s": 12.0,
        "n_eligible": 40,
        "coverage_pct": 90.0,
        "skipped_share": 0.1,
    }
    row.update(over)
    return row


def test_line_summary_passes_exact_values_through() -> None:
    df = pd.DataFrame([_window_row("Red"), _window_row("Red", window_label="other")])
    s = overview.line_summary(df).set_index("line")
    assert len(s) == 1
    assert s.loc["Red", "median_deviation_s"] == 105.0
    assert s.loc["Red", "p90_deviation_s"] == 200.0
    assert s.loc["Red", "coverage_pct"] == 90.0


def test_line_summary_handles_missing_deviation() -> None:
    df = pd.DataFrame([_window_row("Red", median_predicted_deviation_s=None, n_eligible=0)])
    assert overview.line_summary(df)["median_deviation_s"].isna().all()


def test_heatmap_cells_carry_coverage(hourly: pd.DataFrame) -> None:
    cells = explorer.heatmap(hourly, "Red", 0)
    assert set(cells["weekday"]) == {"Sun"}
    assert cells.set_index("hour_local").loc[10, "median_deviation_s"] == 120.0
    matrix = explorer.heatmap_matrix(cells)
    assert list(matrix.index) == ["09:00", "10:00"] and list(matrix.columns) == ["Sun"]


def test_deviation_by_hour_uses_plain_labels(hourly: pd.DataFrame) -> None:
    out = explorer.deviation_by_hour(hourly, "Red", None)
    assert out["label"].tolist() == ["2026-11-22 09:00", "2026-11-22 10:00"]


def _stops() -> tuple[pd.DataFrame, pd.DataFrame]:
    def row(stop: str, seq: float, dev: float | None, cov: int) -> dict[str, object]:
        return {
            "line": "Red",
            "direction_id": 0,
            "stop_id": stop,
            "service_date": date(2026, 11, 22),
            "stop_sequence": seq,
            "median_predicted_deviation_s": dev,
            "n_eligible": 5,
            "coverage_numerator": cov,
            "coverage_denominator": 10,
        }

    stops = pd.DataFrame(
        [row("a", 1, 0.0, 10), row("b", 2, 50.0, 9), row("c", 3, 400.0, 3), row("d", 4, 420.0, 9),
         row("e", 5, 500.0, 9)]
    )  # fmt: skip
    dim = pd.DataFrame({"stop_id": list("abcde"), "stop_name": [s.upper() for s in "abcde"]})
    return stops, dim


def test_slowest_stretches_need_both_stops_well_covered() -> None:
    stops, dim = _stops()
    out = explorer.slowest_stretches(stops, dim, "Red", 0)
    assert list(zip(out["from_stop"], out["to_stop"], strict=True)) == [("D", "E"), ("A", "B")]
    assert out["rise_s"].tolist() == [80.0, 50.0]  # b->c and c->d skipped: c has 30% coverage
    assert (out["min_coverage_pct"] >= 80).all()


def test_slowest_stretches_empty_when_too_few_stops() -> None:
    stops, dim = _stops()
    assert explorer.slowest_stretches(stops.head(1), dim, "Red", 0).empty


def test_export_roundtrip_frames(export_root: Path) -> None:
    base = f"file://{export_root}"
    window = data.load_table(base, "agg_line_reliability_window")
    assert not overview.line_summary(window).empty
    stops = data.load_table(base, "agg_stop_deviation_daily")
    dim = data.load_table(base, "dim_stop")
    line = stops["line"].iloc[0]
    explorer.slowest_stretches(stops, dim, line, int(stops["direction_id"].iloc[0]))

    pipeline = data.load_table(base, "agg_pipeline_health")
    cap = health.captures_per_hour(pipeline)
    assert (cap["runs"] <= cap["expected"] + 1).all()
    assert health.quarantine_by_reason(pipeline)["reason"].tolist()[-1] == "other"
    assert set(health.duplicates_by_feed(pipeline)["feed"]) == set(health.FEEDS)

    checks = data.load_table(base, "agg_quality_checks_hourly")
    summary = health.check_summary(checks)
    assert sum(summary.values()) > 0
    assert not health.check_series(checks, "late_event_share").empty

    snap = data.load_snapshot(base)
    vehicles = livemap.vehicles_frame(snap, dim)
    assert len(vehicles) == snap["vehicle_count"]
    paths = livemap.route_paths(data.load_table(base, "route_shapes"))
    assert not paths.empty and len(paths["path"].iloc[0][0]) == 2


def test_captures_per_hour_marks_gaps() -> None:
    times = pd.date_range("2026-11-22 14:00", periods=60, freq="min", tz="UTC").delete([10, 11])
    df = pd.DataFrame({"run_id": [f"r{i}" for i in range(len(times))], "scheduled_at": times})
    out = health.captures_per_hour(df)
    assert out["runs"].tolist() == [58] and out["expected"].tolist() == [60]
    assert out["missed"].tolist() == [2]


def test_cost_summary_uses_latest_day_and_orders_services() -> None:
    cost = pd.DataFrame(
        {
            "date": [date(2026, 11, 20)] * 2 + [date(2026, 11, 21)] * 2,
            "service": ["Lambda", "S3", "S3", "Lambda"],
            "cost_usd": [0.2, 0.1, 0.05, 0.25],
            "day_total_usd": [0.3, 0.3, 0.3, 0.3],
            "captures": [3000, 3000, None, None],
            "cost_per_1000_captures": [0.1, 0.1, None, None],
        }
    )
    out = health.cost_summary(cost)
    assert out["latest_date"] == date(2026, 11, 21)
    assert out["last_7_days_usd"] == 0.6
    assert out["cost_per_1000_captures"] is None
    assert list(out["by_service"]["service"]) == ["Lambda", "S3"]


def test_slowest_stretches_only_positive_rises_whole_seconds() -> None:
    stops, dim = _stops()
    stops.loc[stops["stop_id"] == "e", "median_predicted_deviation_s"] = 10.4  # falls after d
    out = explorer.slowest_stretches(stops, dim, "Red", 0)
    assert (out["rise_s"] > 0).all() and out["rise_s"].dtype.kind == "i"


def test_direction_labels_use_terminal_with_fallback() -> None:
    stops, dim = _stops()
    assert explorer.direction_labels(stops, dim, "Red", [0])[0] == "Direction 0 (toward E)"
    assert explorer.direction_labels(stops, dim.iloc[0:0], "Red", [0, 1]) == {
        0: "Direction 0",
        1: "Direction 1",
    }


def test_heatmap_buckets_by_service_day() -> None:
    from conftest import hourly_row

    cells = explorer.heatmap(pd.DataFrame([hourly_row("Red", 1)]), "Red", 0)
    assert cells["weekday"].tolist() == ["Sun"]  # 01:00 after Sunday's service date is Sunday's


def test_quarantine_falls_back_to_total_and_partial_hour_flag() -> None:
    p = pd.DataFrame({"quarantine_total": [3, 2], "quarantine_other": [1, 0]})
    out = health.quarantine_by_reason(p)
    assert out["reason"].tolist() == ["other", "not itemised in this export"]
    assert out["records"].tolist() == [1, 4]
    times = pd.date_range("2026-11-22 14:00", periods=90, freq="min", tz="UTC")
    df = pd.DataFrame({"run_id": [f"r{i}" for i in range(90)], "scheduled_at": times})
    now = pd.Timestamp("2026-11-22 15:10", tz="UTC").to_pydatetime()
    assert health.captures_per_hour(df, now)["partial"].tolist() == [False, True]


def test_quarantine_lists_every_reason_by_name_when_the_table_exists() -> None:
    p = pd.DataFrame({"quarantine_total": [9], "quarantine_other": [4]})
    reasons = pd.DataFrame(
        {
            "local_date": ["2026-11-22"] * 3,
            "source_table": ["trip_updates", "trip_updates", "vehicle_positions"],
            "reason": ["missing_trip_id", "schema_mismatch", "missing_trip_id"],
            "n_records": [3, 4, 2],
        }
    )
    out = health.quarantine_by_reason(p, reasons)
    assert out["reason"].tolist() == ["missing_trip_id", "schema_mismatch"]
    assert out["records"].tolist() == [5, 4]


def test_alerts_duplicates_are_labelled_expected_and_no_service_is_a_status() -> None:
    p = pd.DataFrame(
        {
            f"{f}_{c}": v
            for f in health.FEEDS
            for c, v in (("status", ["ok", "ok"]), ("is_duplicate", [True, False]))
        }
    )
    out = health.duplicates_by_feed(p).set_index("feed")["note"]
    assert out["alerts"] == "unchanged feed (expected)"
    assert out["trip_updates"] == ""
    assert "no_service" in health.STATUS_ORDER
