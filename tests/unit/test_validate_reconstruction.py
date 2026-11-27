"""validate_reconstruction: matching rules and statistics, on tiny synthetic tables."""

import importlib.util
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "validate_reconstruction", ROOT / "scripts" / "validate_reconstruction.py"
)
assert _spec and _spec.loader
vr = importlib.util.module_from_spec(_spec)
sys.modules["validate_reconstruction"] = vr
_spec.loader.exec_module(vr)


def _con(ours: list[str], mbta: list[str]) -> duckdb.DuckDBPyConnection:
    """ours rows: row_id, trip, stop, arrival, scheduled; mbta rows: mid, trip, stop, ts, sched.
    Times are seconds after 2026-11-23 00:00 UTC (the zone is irrelevant to the matching)."""
    con = duckdb.connect()
    con.execute(
        """create table ours (row_id bigint, service_date date, line varchar, trip_id varchar,
        direction_id int, stop_id varchar, arrival_ts timestamptz, scheduled_ts timestamptz,
        method varchar, confidence varchar, interval_width_s double, bracket_gap_s double,
        period varchar)"""
    )
    con.execute(
        """create table mbta (mid bigint, service_date date, line varchar, trip_id varchar,
        direction_id int, stop_id varchar, mbta_ts timestamptz, dwell_s bigint,
        mbta_sched_ts timestamptz)"""
    )
    for r in ours:
        con.execute(f"insert into ours values ({r})")
    for r in mbta:
        con.execute(f"insert into mbta values ({r})")
    return con


def t(sec: int | None) -> str:
    return (
        "null" if sec is None else f"timestamptz '2026-11-23 00:00:00+00' + interval {sec} second"
    )


def o(row: int, trip: str, stop: str, arr: int | None, sched: int, w: float = 30.0) -> str:
    return (
        f"{row}, date '2026-11-23', 'Red', '{trip}', 0, '{stop}', {t(arr)}, {t(sched)}, "
        f"'observed', 'high', {w}, null, '1 min'"
    )


def m(mid: int, trip: str, stop: str, ts: int | None, sched: int | None) -> str:
    return f"{mid}, date '2026-11-23', 'Red', '{trip}', 0, '{stop}', {t(ts)}, 20, {t(sched)}"


def pairs(con: duckdb.DuckDBPyConnection) -> set[tuple[int, int, int]]:
    return set(con.execute("select row_id, mid, pass from pairs").fetchall())


def test_greedy_assign_is_one_to_one_and_cheapest_first() -> None:
    got = vr.greedy_assign([(1, 10, 5.0), (1, 11, 1.0), (2, 11, 2.0), (2, 10, 9.0)])
    assert sorted(got) == [(1, 11), (2, 10)]


def test_pass1_matches_on_trip_and_stop() -> None:
    con = _con([o(1, "A", "s1", 100, 100)], [m(1, "A", "s1", 130, 100), m(2, "A", "s2", 500, 400)])
    assert vr.match(con)["pass1"] == 1
    assert pairs(con) == {(1, 1, 1)}


def test_pass1_rejects_pair_further_than_ten_minutes_apart() -> None:
    con = _con([o(1, "A", "s1", 100, 100)], [m(1, "A", "s1", 100 + 601, 100)])
    vr.match(con)
    assert pairs(con) == set()


def test_pass1_picks_nearest_run_when_trip_id_is_reused() -> None:
    con = _con(
        [o(1, "A", "s1", 100, 100), o(2, "A", "s1", 4000, 4000)],
        [m(1, "A", "s1", 4010, 4000), m(2, "A", "s1", 110, 100)],
    )
    vr.match(con)
    assert pairs(con) == {(1, 2, 1), (2, 1, 1)}
    assert con.execute("select count(*) from pairs").fetchone() == (2,)


def test_pass2_pairs_by_nearest_scheduled_arrival_when_trip_ids_differ() -> None:
    con = _con(
        [o(1, "A", "s1", 100, 100)],
        [m(1, "B", "s1", 120, 90), m(2, "C", "s1", 125, 400)],
    )
    counters = vr.match(con)
    assert counters["pass2"] == 1
    assert pairs(con) == {(1, 1, 2)}


def test_pass2_ignores_schedule_gap_over_two_minutes() -> None:
    con = _con([o(1, "A", "s1", 100, 100)], [m(1, "B", "s1", 120, 100 + 121)])
    vr.match(con)
    assert pairs(con) == set()


def test_accuracy_statistics_and_sign() -> None:
    # ours minus LAMP: -10, -20, +40, nothing for the fourth (unmatched)
    con = _con(
        [
            o(1, "A", "s1", 90, 100),
            o(2, "B", "s1", 180, 200),
            o(3, "C", "s1", 340, 300),
            o(4, "D", "s1", 500, 500),
        ],
        [m(1, "A", "s1", 100, 100), m(2, "B", "s1", 200, 200), m(3, "C", "s1", 300, 300)],
    )
    vr.match(con)
    header, rows = vr.accuracy(con, "'ALL'")
    row = dict(zip(header, rows[0], strict=True))
    assert row["ours"] == 4
    assert row["matched"] == 3
    assert row["match %"] == 75.0
    assert row["median abs s"] == 20.0
    assert row["median bias s"] == -10.0
    assert row["within 30 s %"] == 66.7
    assert row["within 60 s %"] == 100.0


def test_accuracy_leaves_out_rows_without_an_arrival() -> None:
    con = _con([o(1, "A", "s1", None, 100)], [m(1, "A", "s1", 100, 100)])
    vr.match(con)
    _, rows = vr.accuracy(con, "'ALL'")
    assert rows == []


def test_interval_end_shift_aligns_observed_rows_with_the_same_message() -> None:
    # midpoint 100, width 40 => interval end 120 == LAMP's stop_timestamp
    con = _con([o(1, "A", "s1", 100, 100, w=40.0)], [m(1, "A", "s1", 120, 100)])
    vr.match(con)
    _, rows = vr.accuracy(
        con, "'ALL'", diff="epoch(o.arrival_ts) + o.interval_width_s / 2 - epoch(m.mbta_ts)"
    )
    assert rows[0][6] == 0.0  # median bias


def test_coverage_reasons() -> None:
    con = _con(
        [
            o(1, "A", "s1", 100, 100),
            o(2, "X", "s1", 100, 100),
            o(3, "A", "s9", 100, 100),
            o(4, "A", "s2", None, 100),
        ],
        [m(1, "A", "s1", 100, 100), m(2, "A", "s2", 100, 100)],
    )
    vr.match(con)
    got = dict(vr.coverage(con)["ours"])
    assert got["matched"] == 1
    assert got["trip not in LAMP"] == 1
    assert got["stop not in LAMP trip"] == 1
    assert got["no arrival_ts (missing), LAMP has a time"] == 1
