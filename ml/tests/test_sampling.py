from datetime import timedelta

import pandas as pd
from headway_ml import sampling
from synth import START


def frame(n_big: int = 400, n_small: int = 8) -> pd.DataFrame:
    rows = []
    for day in range(3):
        for line, n in (("Red", n_big), ("Orange", n_big), ("Mattapan", n_small)):
            for h in (15, 30, 60):
                for i in range(n):
                    rows.append(
                        {"service_date": START + timedelta(days=day), "line": line,
                         "horizon_min": h, "x": i}
                    )  # fmt: skip
    return pd.DataFrame(rows)


def test_small_lines_keep_all_rows_and_big_lines_share_the_rest() -> None:
    df = frame()
    out, stats = sampling.stratified_cap(df, 600, seed=7)
    per_day = out.groupby("service_date").size()
    assert per_day.max() <= 600 + 9  # at most one extra row per stratum from the floor of 1
    small = out[out["line"] == "Mattapan"]
    assert len(small) == len(df[df["line"] == "Mattapan"])  # every Mattapan row kept
    big = out[out["line"] == "Red"].groupby(["service_date", "horizon_min"]).size()
    assert big.min() > 40  # budget left by Mattapan goes to the large strata
    s = stats.set_index(["line", "horizon_min"])
    assert (s.loc[("Mattapan", 15), ["n_before", "n_after"]] == [24, 24]).all()
    assert s["n_after"].sum() == len(out)


def test_same_seed_same_sample_and_every_stratum_survives() -> None:
    df = frame()
    a, _ = sampling.stratified_cap(df, 30, seed=7)
    b, _ = sampling.stratified_cap(df, 30, seed=7)
    c, _ = sampling.stratified_cap(df, 30, seed=8)
    pd.testing.assert_frame_equal(a, b)
    assert not a.index.equals(c.index)
    assert a.groupby(["service_date", "line", "horizon_min"]).ngroups == (
        df.groupby(["service_date", "line", "horizon_min"]).ngroups
    )


def test_no_cap_and_small_days_are_untouched() -> None:
    df = frame(n_big=2, n_small=1)
    out, stats = sampling.stratified_cap(df, None, seed=7)
    pd.testing.assert_frame_equal(out, df)
    assert (stats["n_before"] == stats["n_after"]).all()
    out, _ = sampling.stratified_cap(df, 10_000, seed=7)
    assert len(out) == len(df)


def test_allocation_redistributes_unused_budget() -> None:
    quota = sampling.allocate({("a", 15): 5, ("b", 15): 100, ("c", 15): 100}, 105)
    assert quota[("a", 15)] == 5 and quota[("b", 15)] == 50 and quota[("c", 15)] == 50
