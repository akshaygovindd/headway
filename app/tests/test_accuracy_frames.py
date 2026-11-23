"""Frames for the Release 2 pages: weighting, coverage, withholding and rounding helpers."""

from datetime import date

import pandas as pd

import ui
from lib import accuracy, data


def _acc(**over: object) -> dict[str, object]:
    row: dict[str, object] = {
        "line": "Red", "service_date": date(2026, 11, 22), "horizon_bucket": "0-5",
        "service_disruption": False, "n": 10, "n_excluded_outage": 0,
        "median_abs_error_s": 20.0, "mae_s": 30.0, "pct_within_60s": 90.0, "pct_within_120s": 100.0,
        "coverage_numerator": 10, "coverage_denominator": 20,
    }  # fmt: skip
    row.update(over)
    return row


def test_accuracy_weights_by_n_and_sums_coverage() -> None:
    df = pd.DataFrame([
        _acc(),
        _acc(service_date=date(2026, 11, 23), n=30, median_abs_error_s=40.0, coverage_numerator=30),
        _acc(horizon_bucket="10-20", median_abs_error_s=70.0),
        _acc(service_disruption=True, n=10, median_abs_error_s=100.0),
    ])  # fmt: skip
    out = accuracy.accuracy_by_horizon(df).set_index("horizon_bucket")
    first = out.loc["0-5"]
    assert first["n"] == 50 and first["n_disruption"] == 10
    assert round(first["median_abs_error_s"], 1) == round((20 * 10 + 40 * 30 + 100 * 10) / 50, 1)
    assert first["coverage_pct"] == 100.0 * (10 + 30 + 10) / (20 + 20 + 20)
    assert list(out.index) == ["0-5", "10-20"]  # bucket order, not alphabetical


def test_values_below_half_coverage_are_withheld() -> None:
    assert accuracy.shown(12.0, 49.9) is None
    assert accuracy.shown(12.0, None) is None
    assert accuracy.shown(12.0, 50.0) == 12.0


def test_actual_deviation_skips_outage_hours_and_reports_them() -> None:
    def row(hour: int, outage: bool, **over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "line": "Red", "data_outage": outage, "service_disruption": False,
            "n_arrivals": 0 if outage else 10, "n_excluded_outage": 5 if outage else 0,
            "median_deviation_s": None if outage else 60.0,
            "p90_deviation_s": None if outage else 200.0,
            "pct_on_time_window": None if outage else 80.0,
            "coverage_numerator": 0 if outage else 10, "coverage_denominator": 12,
        }  # fmt: skip
        base.update(over)
        return base

    out = accuracy.actual_deviation_summary(pd.DataFrame([row(9, False), row(10, True)]))
    r = out.iloc[0]
    assert r["n"] == 10 and r["n_excluded_outage"] == 5 and r["median_deviation_s"] == 60.0
    assert r["coverage_pct"] == 100.0 * 10 / 12  # the outage hour's denominator is not counted


def test_headway_summary_dropped_counts_and_weights() -> None:
    def row(hour: int, n: int, within: float, **over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "line": "Red", "direction_id": 0, "hour_local": hour, "data_outage": False,
            "service_disruption": False, "n_headways": n, "pct_within_band": within,
            "pct_bunched": 100 - within, "pct_gap": 0.0, "median_ratio": 1.0,
            "n_dropped_train_between": 2, "n_dropped_unproven_order": 1,
            "n_dropped_no_scheduled_headway": 0, "n_dropped_implausible": 0,
            "n_excluded_outage": 0, "coverage_numerator": n, "coverage_denominator": 20,
        }  # fmt: skip
        base.update(over)
        return base

    s = accuracy.headway_summary(pd.DataFrame([row(9, 10, 80.0), row(10, 30, 60.0)]), "Red", None)
    assert s["n"] == 40 and s["n_dropped"] == 6
    assert s["pct_within_band"] == (80 * 10 + 60 * 30) / 40
    assert s["coverage_pct"] == 100.0


def test_arrival_coverage_note() -> None:
    recon = pd.DataFrame([{"line": "Red", "trip_stops": 100, "n_high": 10, "n_medium": 70}])
    note = accuracy.arrival_coverage_note(recon, "Red")
    assert note.startswith(
        "Reconstruction confidence: 80% high/medium of 100 reconstructed trip stops"
    )
    assert accuracy.arrival_coverage_note(None) == "Reconstruction confidence: n/a."


def test_heavy_rail_set() -> None:
    assert accuracy.is_heavy_rail("Orange") and not accuracy.is_heavy_rail("Green-B")
    assert not accuracy.is_heavy_rail("Mattapan")


def test_rounding_helpers() -> None:
    assert ui.fmt_pct(79.6) == "80%" and ui.fmt_pct(None) == "n/a"
    assert ui.fmt_ratio(1.234) == "1.23×"
    assert ui.coverage_text(81.2, 1234) == "coverage 81%, n=1,234"


def test_r2_tables_are_known_to_the_loader() -> None:
    assert set(data.R2_TABLES).isdisjoint(data.GOLD_TABLES)


def test_window_frames_pass_the_exact_values_through() -> None:
    win = pd.DataFrame([
        {"line": "Red", "window_label": "last_24h", "horizon_bucket": "10-20", "n": 4,
         "median_abs_error_s": 11.0, "mae_s": 12.0, "pct_within_60s": 90.0, "pct_within_120s": 99.0,
         "n_disruption": 1, "coverage_pct": 80.0},
        {"line": "Red", "window_label": "last_24h", "horizon_bucket": "0-5", "n": 6,
         "median_abs_error_s": 5.0, "mae_s": 6.0, "pct_within_60s": 95.0, "pct_within_120s": 100.0,
         "n_disruption": 0, "coverage_pct": 70.0},
        {"line": "Red", "window_label": "last_7d", "horizon_bucket": "0-5", "n": 60,
         "median_abs_error_s": 9.0, "mae_s": 9.0, "pct_within_60s": 9.0, "pct_within_120s": 9.0,
         "n_disruption": 0, "coverage_pct": 60.0},
    ])  # fmt: skip
    out = accuracy.accuracy_window_by_horizon(win, "last_24h")
    assert list(out["horizon_bucket"]) == ["0-5", "10-20"]  # bucket order
    assert list(out["median_abs_error_s"]) == [5.0, 11.0]  # taken as is, not weighted


def test_headway_window_summary_matches_the_summary_keys() -> None:
    win = pd.DataFrame([{
        "line": "Red", "window_label": "last_7d", "n_headways": 40, "n_disruption": 2,
        "pct_within_band": 50.0, "pct_bunched": 25.0, "pct_gap": 25.0, "median_ratio": 1.0,
        "n_dropped_train_between": 3, "n_dropped_unproven_order": 1,
        "n_dropped_no_scheduled_headway": 0, "n_dropped_implausible": 1,
        "n_excluded_outage": 7, "coverage_pct": 66.0,
    }])  # fmt: skip
    s = accuracy.headway_window_summary(win, "Red", "last_7d")
    assert s["n"] == 40.0 and s["n_dropped"] == 5.0 and s["n_excluded_outage"] == 7.0
    assert s["pct_within_band"] == 50.0 and s["coverage_pct"] == 66.0 and s["n_disruption"] == 2.0
    missing = accuracy.headway_window_summary(win, "Blue", "last_7d")
    assert missing["n"] == 0.0 and missing["pct_within_band"] is None


def test_window_options_offer_exact_windows_only_when_all_tables_exist() -> None:
    table = pd.DataFrame({"x": [1]})
    assert list(accuracy.window_options(table, table)) == ["last_24h", "last_7d", "all_days"]
    assert list(accuracy.window_options(table, None)) == ["all_days"]
    assert list(accuracy.window_options(table, pd.DataFrame())) == ["all_days"]


def _slo(name: str, pct: float, met: bool, counts: bool = True) -> dict[str, object]:
    return {"slo_name": name, "service_date": date(2026, 11, 22), "target_pct": 99.0,
            "actual_pct": pct, "met": met, "counts_toward_slo": counts}  # fmt: skip


def test_slo_table_shows_latest_7_and_30_day_values_and_separates_disrupted_lines() -> None:
    from lib import health

    daily = pd.DataFrame([
        _slo("capture_completeness", 99.5, True),
        _slo("arrival_coverage_disrupted", 50.0, False, counts=False),
        _slo("arrival_coverage", 81.0, True),
    ])  # fmt: skip
    roll = pd.DataFrame([
        {"slo_name": "capture_completeness", "window_label": "last_7d", "target_pct": 99.0,
         "actual_pct": 98.5, "met": False, "error_budget_used": 1.5, "days_covered": 7,
         "total_events": 1680, "counts_toward_slo": True},
    ])  # fmt: skip
    out = health.slo_table(daily, roll).set_index("Objective")
    row = out.loc["Capture completeness (minutes)"]
    assert row["Latest date"] == "✅ 99.50%"
    assert row["Last 7 days (based on 28 h)"] == "❌ 98.50% · budget 150%"
    assert row["Last 30 days"] == "no data yet (no runs recorded)"
    assert list(out["Counts"])[-1] == "reported separately"  # listed last, not hidden
    assert len(out) == 5  # all four objectives and the informational one, even without events
    assert (
        out.loc["Gold freshness ≤ 90 min (hours)", "Latest date"]
        == "no data yet (no runs recorded)"
    )


def test_slo_table_without_any_runs_still_lists_every_objective() -> None:
    from lib import health

    out = health.slo_table(pd.DataFrame(), pd.DataFrame())
    assert len(out) == 5
    assert set(out["Latest date"]) == {"no data yet (no runs recorded)"}
    assert list(out["Counts"]).count("reported separately") == 1


def test_withholding_rule_is_one_predicate() -> None:
    from lib.coverage import is_withheld

    assert is_withheld(49.9) and is_withheld(None) and is_withheld(float("nan"))
    assert not is_withheld(50.0)
    assert ui.cell_seconds(12.0, 49.0) == "withheld (<50% coverage)"
    assert ui.cell_pct(12.0, 80.0) == "12%"


def test_headway_by_hour_runs_05_to_04_and_labels_missing_hours() -> None:
    def row(hour: int, outage: bool = False, cov: int = 9) -> dict[str, object]:
        return {"line": "Red", "direction_id": 0, "hour_local": hour, "data_outage": outage,
                "n_headways": 10, "pct_within_band": 70.0,
                "coverage_numerator": cov, "coverage_denominator": 10}  # fmt: skip

    hw = pd.DataFrame([row(1), row(5), row(9), row(10, outage=True), row(11, cov=3)])
    out = accuracy.headway_by_hour(hw, "Red", None)
    assert list(out["hour_local"]) == [*range(5, 24), 0, 1, 2, 3, 4]
    status = dict(zip(out["hour_local"], out["status"], strict=True))
    assert status[5] == "ok" and status[1] == "ok" and status[9] == "ok"
    assert status[10] == "data outage" and status[11] == "withheld" and status[12] == "no data"
    assert pd.isna(out.set_index("hour_local").loc[11, "pct_within_band"])


def test_disrupted_lines_follow_the_window() -> None:
    alerts = pd.DataFrame([
        {"line": "Red", "service_date": date(2026, 11, 22), "hour_local": 12,
         "effect": "DETOUR", "is_service_disruption": True},
        {"line": "Blue", "service_date": date(2026, 11, 10), "hour_local": 12,
         "effect": "NO_SERVICE", "is_service_disruption": True},
        {"line": "Orange", "service_date": date(2026, 11, 22), "hour_local": 12,
         "effect": "ACCESSIBILITY_ISSUE", "is_service_disruption": False},
    ])  # fmt: skip
    as_of = "2026-11-23T20:00:00+00:00"
    assert accuracy.disrupted_lines(alerts, as_of, "last_24h") == {}
    assert accuracy.disrupted_lines(alerts, as_of, "last_7d") == {"Red": ["detour"]}
    assert accuracy.disrupted_lines(alerts, as_of, "all_days") == {
        "Blue": ["no service"], "Red": ["detour"]
    }  # fmt: skip
    assert accuracy.disrupted_lines(None, as_of, "all_days") == {}
    assert accuracy.disruption_label(["detour"]) == "Service disruption: detour"


def test_window_labels_give_different_rows() -> None:
    win = pd.DataFrame([
        {"line": "Red", "window_label": label, "horizon_bucket": "0-5", "n": n,
         "median_abs_error_s": m, "mae_s": m, "pct_within_60s": 50.0, "pct_within_120s": 60.0,
         "n_disruption": 0, "coverage_pct": 80.0}
        for label, n, m in (("last_24h", 10, 20.0), ("last_7d", 70, 35.0))
    ])  # fmt: skip
    a = accuracy.accuracy_window_by_horizon(win, "last_24h").iloc[0]
    b = accuracy.accuracy_window_by_horizon(win, "last_7d").iloc[0]
    assert (a["n"], a["median_abs_error_s"]) != (b["n"], b["median_abs_error_s"])
