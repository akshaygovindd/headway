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
            "median_deviation_s": None if outage else 60.0, "p90_deviation_s": None if outage else 200.0,
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
    assert "80%" in accuracy.arrival_coverage_note(recon, "Red")
    assert accuracy.arrival_coverage_note(None) == "Arrival coverage: n/a."


def test_heavy_rail_set() -> None:
    assert accuracy.is_heavy_rail("Orange") and not accuracy.is_heavy_rail("Green-B")
    assert not accuracy.is_heavy_rail("Mattapan")


def test_rounding_helpers() -> None:
    assert ui.fmt_pct(79.6) == "80%" and ui.fmt_pct(None) == "n/a"
    assert ui.fmt_ratio(1.234) == "1.23×"
    assert ui.coverage_text(81.2, 1234) == "coverage 81%, n=1,234"


def test_r2_tables_are_known_to_the_loader() -> None:
    assert set(data.R2_TABLES).isdisjoint(data.GOLD_TABLES)
