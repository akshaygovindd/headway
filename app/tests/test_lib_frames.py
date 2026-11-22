from datetime import date
from pathlib import Path

import pandas as pd

from lib import data, explorer, health, livemap, overview
from tests.conftest import hourly_row


def test_line_summary_weights_by_eligible_predictions(hourly: pd.DataFrame) -> None:
    s = overview.line_summary(hourly).set_index("line")
    # Red: (60*10 + 120*30) / 40
    assert s.loc["Red", "median_deviation_s"] == 105.0
    assert s.loc["Red", "coverage_pct"] == 90.0
    assert s.loc["Red", "skipped_share"] == 0.1
    assert s.loc["Red", "hours"] == 2
    assert s.loc["Orange", "coverage_pct"] == 40.0


def test_line_summary_window_keeps_recent_hours() -> None:
    df = pd.DataFrame(
        [hourly_row("Red", 9), hourly_row("Red", 20, median_predicted_deviation_s=300.0)]
    )
    s = overview.line_summary(df, window_hours=3).set_index("line")
    assert s.loc["Red", "median_deviation_s"] == 300.0
    assert s.loc["Red", "hours"] == 1


def test_line_summary_handles_missing_deviation() -> None:
    df = pd.DataFrame([hourly_row("Red", 9, median_predicted_deviation_s=None, n_eligible=0)])
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
    hourly = data.load_table(base, "agg_line_reliability_hourly")
    assert not overview.line_summary(hourly).empty
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
