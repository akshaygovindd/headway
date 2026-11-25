"""A small synthetic gold export (the tables the public API reads) for the API tests and smoke."""

import json
from datetime import date
from pathlib import Path

import duckdb

DAY = date(2026, 11, 22)
EXPORTED_AT = "2026-11-23T00:00:00Z"
LINE_COV = (("Red", 90.0), ("Orange", 80.0), ("Blue", 30.0))


def _w(con: duckdb.DuckDBPyConnection, path: Path, sql: str) -> None:
    con.execute(f"copy ({sql}) to '{path}' (format parquet)")


def _rows(template: str, items: list[tuple[object, ...]]) -> str:
    return " union all ".join(template.format(*i) for i in items)


def build(base: Path, exported_at: str = EXPORTED_AT, shift: float = 0.0) -> str:
    """Write <base>/gold and return the data root URL. `shift` moves the Red median (new export)."""
    gold = base / "gold"
    gold.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    tables = {
        "agg_line_reliability_window": _rows(
            "select '{0}' line, 'last_24h' window_label, {1}::double median_predicted_deviation_s,"
            " 100.0::double p90_predicted_deviation_s, 5.0::double median_abs_revision_s,"
            " 10 n_eligible, {2}::double coverage_pct, 0.1::double skipped_share",
            [(ln, (60.0 + shift) if ln == "Red" else 20.0, cov) for ln, cov in LINE_COV],
        ),
        "agg_actual_deviation_window": _rows(
            "select '{0}' line, '{1}' window_label, 30.0::double median_deviation_s,"
            " 200.0::double p90_deviation_s, 80.0::double pct_on_time_window, 12 n_arrivals,"
            " 100 scheduled_trip_stops, {2}::double coverage_pct",
            [(ln, w, cov) for ln, cov in LINE_COV for w in ("last_24h", "last_7d")],
        ),
        "agg_accuracy_window": _rows(
            "select '{0}' line, 'last_24h' window_label, '{1}' horizon_bucket, 9 n,"
            " 40.0::double median_abs_error_s, 120.0::double p90_abs_error_s, 55.0::double mae_s,"
            " 70.0::double pct_within_60s, 85.0::double pct_within_120s, {2}::double coverage_pct",
            [(ln, h, cov) for ln, cov in LINE_COV for h in ("0-5 min", "5-15 min")],
        ),
        "agg_headway_window": _rows(
            "select '{0}' line, 'last_24h' window_label, 7 n_headways, 60.0::double"
            " pct_within_band, 25.0::double pct_bunched, 15.0::double pct_gap,"
            " 1.0::double median_ratio, 1.4::double p90_ratio, {1}::double coverage_pct",
            [(ln, cov) for ln, cov in LINE_COV],
        ),
        "agg_countdown_accuracy_window": _rows(
            "select '{0}' line, '{1}' station_id, 'Station {1}' station_name,"
            " 'last_24h' window_label, 2 countdown_min, 20 n, 50.0::double pct_within_60s,"
            " 75.0::double pct_within_120s, {2}::double coverage_pct",
            [(ln, st, cov) for ln, cov in LINE_COV for st in ("place-a", "place-b")],
        ),
        "agg_incidents": (
            "select 'inc-1' incident_id, 'Red' line, 0 direction_id,"
            f" timestamptz '{DAY} 10:00:00-05' incident_start_ts,"
            f" timestamptz '{DAY} 12:00:00-05' incident_end_ts, 2 n_hours, false is_open,"
            " ['p90_deviation'] thresholds_crossed, 62.5::double min_coverage_pct,"
            " 70.0::double mean_coverage_pct, 0 n_outage_hours_inside, 'matched' match_status,"
            " 120 alert_lag_s, 'Delays on the Red Line.' summary_text, true template_used,"
            " 'secret evidence' evidence_json"
        ),
        "agg_forecast_next_hour": _rows(
            "select 'Red' line, 0 direction_id, 'stop{0}' stop_id,"
            f" timestamptz '{DAY} 15:00:00-05' prediction_ts, 15 horizon_min, 4 n_trips,"
            " null::double model_forecast_s, 0 n_model, 30.0::double persistence,"
            " 4 n_persistence, 20.0::double daytype_hour, {1} n_daytype_hour,"
            " 25.0::double rolling_median_60m, 4 n_rolling_median_60m,"
            " 10.0::double mbta_reference, 4 n_mbta_reference, 'baselines_only' forecast_status,"
            " 'no approved model for this line' status_reason, null::integer model_version",
            [(1, 4), (2, 0)],
        ),
        "agg_forecast_gate_status": (
            "select 'Red' line, 5 n_days, 1 n_weekend_days, 21 min_days, 6 min_weekend_days,"
            " false gate_passed, 'training_job' as source"
        ),
        "agg_headway_segment_window": _rows(
            "select '{0}' line, {1} direction_id, '{2}' from_stop_id, '{3}' from_stop_name,"
            " '{4}' to_stop_id, '{5}' to_stop_name, 'last_24h' window_label, {6} segment_order,"
            " {7} n_headways, {8}::double pct_within_band, {9}::double pct_bunched,"
            " {10}::double pct_gap, {11} n_excluded_outage, {12} n_disruption,"
            " {13} disrupted, {14}::double coverage_pct, {15} health",
            [
                # Red: even; some bunching in a disrupted section; bunched gaps at 55% coverage;
                # direction 1 with an outage hour left out
                (
                    "Red",
                    0,
                    "a1",
                    "Alpha",
                    "b1",
                    "Beta",
                    1,
                    10,
                    85.0,
                    10.0,
                    5.0,
                    0,
                    0,
                    "false",
                    90.0,
                    "'even'",
                ),
                (
                    "Red",
                    0,
                    "b1",
                    "Beta",
                    "c1",
                    "Gamma",
                    2,
                    10,
                    65.0,
                    20.0,
                    15.0,
                    0,
                    6,
                    "true",
                    90.0,
                    "'some_bunching'",
                ),
                (
                    "Red",
                    0,
                    "c1",
                    "Gamma",
                    "d1",
                    "Delta",
                    3,
                    6,
                    40.0,
                    30.0,
                    30.0,
                    0,
                    0,
                    "false",
                    55.0,
                    "'bunched_gaps'",
                ),
                (
                    "Red",
                    1,
                    "d2",
                    "Delta",
                    "c2",
                    "Gamma",
                    1,
                    9,
                    90.0,
                    5.0,
                    5.0,
                    3,
                    0,
                    "false",
                    72.0,
                    "'even'",
                ),
                (
                    "Orange",
                    0,
                    "o1",
                    "Oak",
                    "o2",
                    "Maple",
                    1,
                    8,
                    70.0,
                    15.0,
                    15.0,
                    0,
                    0,
                    "false",
                    80.0,
                    "'some_bunching'",
                ),
                # Blue: under 50% coverage, withheld by the API
                (
                    "Blue",
                    0,
                    "u1",
                    "Upper",
                    "u2",
                    "Lower",
                    1,
                    3,
                    100.0,
                    0.0,
                    0.0,
                    0,
                    0,
                    "false",
                    30.0,
                    "'even'",
                ),
            ],
        ),
        "agg_marey_last_hour": _rows(
            "select '{0}' line, {1} direction_id, '{2}' trip_instance_key, 0 segment,"
            " '{2}' trip_id, '{3}' stop_id, '{4}' stop_name, {5} stop_sequence,"
            " timestamptz '2026-11-22 09:{6}:00-05' arrival_ts, '{7}' as \"method\","
            " 'high' confidence,"
            " timestamptz '2026-11-22 09:{8}:00-05' scheduled_ts,"
            " timestamptz '2026-11-22 09:00:00-05' hour_start",
            [
                ("Red", 0, "t1", "a1", "Alpha", 1, "10", "observed", "09"),
                ("Red", 0, "t1", "b1", "Beta", 2, "14", "estimated", "13"),
                ("Red", 1, "t2", "d2", "Delta", 1, "20", "observed", "20"),
                ("Orange", 0, "t3", "o1", "Oak", 1, "30", "observed", "30"),
            ],
        ),
        "agg_capture_heartbeat_hourly": _rows(
            "select date '{0}' service_date, {1} hour_local,"
            " timestamptz '{0} {2}:00:00-05' hour_start, {3} expected_runs, {4} missed_runs,"
            " {5}::double feed_gap_min, {6} data_outage",
            [
                (DAY, 7, "07", 60, 0, 0.0, "false"),
                (DAY, 8, "08", 60, 1, 1.0, "false"),
                (DAY, 9, "09", 60, 22, 18.0, "true"),
            ],
        ),
        "agg_slo_rollup": (
            "select 'transform_success' slo_name, 'last_7d' window_label, 99.0::double target_pct,"
            " 98 good_events, 99 total_events, 98.99::double actual_pct, true met,"
            " 0.5::double error_budget_used, 7 days_covered, true counts_toward_slo"
        ),
        "agg_pipeline_health": (
            f"select timestamptz '{DAY} 10:00:00-05' scheduled_at, 1 vehicle_polls_ok,"
            " 0 vehicle_polls_error, 0 duplicate_delivery_count, 3 quarantine_total,"
            " 'ok' trip_updates_status"
        ),
    }
    counts: dict[str, int] = {}
    for name, sql in tables.items():
        _w(con, gold / f"{name}.parquet", sql)
        counts[name] = con.execute(f"select count(*) from '{gold / f'{name}.parquet'}'").fetchone()[
            0
        ]  # type: ignore[index]
    (gold / "manifest.json").write_text(json.dumps({
        "exported_at": exported_at,
        "metrics_as_of": f"{DAY}T15:00:00Z",
        "row_counts": counts,
    }))  # fmt: skip
    return f"file://{base}"
