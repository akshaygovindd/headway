"""Countdown accuracy frames: counts add up exactly across stations and days."""

import pandas as pd

from lib import accuracy, countdown


def _rows(**over: object) -> list[dict[str, object]]:
    base = {"line": "Red", "countdown_min": 3, "n_arrived": 0, "n_non_arrival": 0, "n_unknown": 0,
            "n_excluded_outage": 0, "n_disruption": 0}  # fmt: skip
    rows = [
        {"station_id": "a", "station_name": "Alpha", "n": 60, "n_within_60s": 30,
         "n_within_120s": 54, "coverage_numerator": 60, "coverage_denominator": 100},
        {"station_id": "b", "station_name": "Bravo", "n": 40, "n_within_60s": 40,
         "n_within_120s": 40, "coverage_numerator": 40, "coverage_denominator": 100},
    ]  # fmt: skip
    return [{**base, **r, **over} for r in rows]


def test_line_row_is_the_exact_sum_of_its_stations() -> None:
    frame = pd.DataFrame(_rows(window_label="last_7d", n_unknown=2))
    line = countdown.by_line(frame, "Red")
    assert len(line) == 1
    row = line.iloc[0]
    # 70 of 100 countdowns, not the mean of 50% and 100%; coverage 100 of 200 trip stops
    assert (row["n"], row["pct_within_60s"], row["pct_within_120s"]) == (100, 70.0, 94.0)
    assert row["coverage_pct"] == 50.0
    assert row["n_unknown"] == 4
    stations = countdown.by_station(frame, "Red")
    assert list(stations["station_name"]) == ["Alpha", "Bravo"]
    assert list(stations["pct_within_60s"]) == [50.0, 100.0]
    assert list(stations["coverage_pct"]) == [60.0, 40.0]


def test_all_days_adds_up_daily_rows_and_a_window_is_picked_by_label() -> None:
    daily = pd.DataFrame(_rows(service_date="2026-11-22") + _rows(service_date="2026-11-23"))
    win = pd.DataFrame(
        _rows(window_label="last_24h") + _rows(window_label="last_7d", n=1, n_within_60s=0)
    )
    both = countdown.by_line(countdown.source(win, daily, accuracy.ALL_DAYS), "Red")
    assert both.iloc[0]["n"] == 200
    assert (countdown.source(win, daily, "last_24h")["window_label"] == "last_24h").all()
    assert countdown.source(None, None, "last_24h").empty
    assert countdown.source(win, None, accuracy.ALL_DAYS).empty


def test_no_countdowns_gives_no_percentage() -> None:
    frame = pd.DataFrame(_rows(n=0, n_within_60s=0, n_within_120s=0, coverage_numerator=0))
    row = countdown.by_line(frame, "Red").iloc[0]
    assert pd.isna(row["pct_within_60s"]) and row["n"] == 0


def test_unchecked_note_counts_unknowns_and_misses() -> None:
    rows = countdown.by_line(pd.DataFrame(_rows(n_unknown=3, n_non_arrival=1)), "Red")
    note = countdown.unchecked_note(rows)
    assert "6 countdowns could not be checked" in note and "2 counted countdowns" in note
