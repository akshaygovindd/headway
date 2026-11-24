"""Simulation 2: scheduler invocations that never happen are counted as missed runs, make the
hour a data outage, and spend the capture-completeness error budget."""

import pytest

from .world import World, at

SLO_AS_OF = "2026-11-22T16:59:30Z"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> World:
    world = World(tmp_path_factory.mktemp("missed_runs"))
    world.drop([at(15, 10), at(15, 20), at(15, 30)])  # three runs, one hour, never invoked
    world.curate()
    world.result = world.build(slo_as_of=SLO_AS_OF)  # type: ignore[attr-defined]
    return world


def test_missing_runs_leave_no_trace_in_the_manifests(built: World) -> None:
    for minute in (at(15, 10), at(15, 20), at(15, 30)):
        assert built.manifest_keys(minute) == [] and not built.claimed(minute)
    runs = built.result.one(  # type: ignore[attr-defined]
        "select count(*) as n from agg_pipeline_health "
        "where scheduled_at >= timestamptz '2026-11-22 15:00:00+00' "
        "and scheduled_at < timestamptz '2026-11-22 16:00:00+00'"
    )
    assert runs["n"] == 57


def test_missed_runs_are_counted_and_warn(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    assert result.check("missed_runs", 10) == {"value": 3.0, "status": "warn"}
    assert result.check("missed_runs", 9) == {"value": 0.0, "status": "ok"}
    assert result.check("feed_gap_min", 10) == {"value": 1.0, "status": "ok"}  # single minutes


def test_three_missed_runs_make_the_hour_a_data_outage_even_with_a_full_window(
    built: World,
) -> None:
    flags = built.result.flags(10)  # type: ignore[attr-defined]
    assert flags["missed_runs"] == 3
    assert flags["vehicle_window_complete"] is True  # 57 capture minutes is a complete window
    assert flags["data_outage"] is True


def test_the_outage_hour_is_excluded_from_the_metrics(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    row = result.one(
        "select sum(n_arrivals) as used, sum(n_excluded_outage) as excluded "
        "from agg_actual_deviation_hourly where hour_local = 10 and data_outage"
    )
    assert row["used"] == 0 and row["excluded"] > 0


def test_capture_completeness_spends_the_error_budget(built: World) -> None:
    row = built.result.one(  # type: ignore[attr-defined]
        "select * from agg_slo_daily where slo_name = 'capture_completeness' "
        "and service_date = date '2026-11-22'"
    )
    assert (row["good_events"], row["total_events"]) == (177, 180)
    assert row["met"] is False
    assert row["error_budget_used"] == pytest.approx(3 / (0.01 * 180))


def test_the_next_hour_is_clean(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    assert result.flags(11)["data_outage"] is False
    assert result.check("missed_runs", 11)["status"] == "ok"
