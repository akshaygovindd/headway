import pandas as pd
from r3_fixtures import backtest_table, gate_table, next_hour_table

from lib import forecast


def test_gate_text_counts_days_and_says_what_unlocks_the_comparison() -> None:
    g = gate_table().set_index("line")
    low = forecast.gate_line(g.loc["Mattapan"])
    assert low == (
        "comparison unlocks after 21 service days incl. 3 weekends; currently 4 days (1 weekend)"
    )
    assert forecast.gate_line(g.loc["Red"]) == "gate passed: 25 service days (6 weekend days)"


def test_next_hour_table_has_the_model_row_only_for_a_model_line() -> None:
    nh = next_hour_table()
    red = forecast.next_hour_table(nh, "Red")
    assert set(red["method"]) == {
        "model", "persistence", "daytype_hour", "rolling_median_60m", "mbta_reference"
    }  # fmt: skip
    mat = forecast.next_hour_table(nh, "Mattapan")
    assert "model" not in set(mat["method"])
    assert forecast.line_status(nh, "Mattapan") == ("baselines_only", "training gate not passed")
    assert forecast.line_status(nh, "Red")[0] == "model"
    assert forecast.feature_age_s(nh, "Red") == 120.0


def test_coverage_is_the_share_of_trip_stops_with_a_value() -> None:
    blue = forecast.next_hour_table(next_hour_table(), "Blue")
    p = blue[(blue["method"] == "persistence") & (blue["horizon_min"] == 15)].iloc[0]
    assert p["coverage_pct"] == 25.0 and p["n"] == 2  # 2 of 8 trip stops
    full = blue[(blue["method"] == "daytype_hour") & (blue["horizon_min"] == 15)].iloc[0]
    assert full["coverage_pct"] == 100.0 and full["value"] == 45.0


def test_banner_wording_follows_the_paired_interval() -> None:
    win = forecast.comparison(backtest_table("win"), "Red", 15)
    assert win is not None and win.verdict == "better"
    assert win.text.startswith("model better by 30 s [+15, +45] s")
    lose = forecast.comparison(backtest_table("lose"), "Red", 15)
    assert lose is not None and lose.verdict == "worse"
    assert lose.text.startswith("model worse by 40 s [+20, +60] s")
    tie = forecast.comparison(backtest_table("tie"), "Red", 15)
    assert tie is not None and tie.verdict == "none"
    assert tie.text.startswith("no significant difference")
    for c in (win, lose, tie):
        assert "Typical for day type and hour (baseline)" in c.text  # the best baseline, named


def test_the_reference_is_never_the_comparison_peer_and_low_coverage_is_withheld() -> None:
    bt = backtest_table("win")
    low = bt.assign(coverage_pct=40.0)
    got = forecast.comparison(low, "Red", 15)
    assert got is not None and got.verdict == "withheld"
    assert forecast.comparison(bt[bt["method"] != "lightgbm"], "Red", 15) is None
    assert forecast.comparison(None, "Red", 15) is None
    ctx = forecast.context_table(bt, "Red")
    assert {"mbta_reference", "lightgbm"} <= set(ctx["method"])
    assert isinstance(ctx, pd.DataFrame)
