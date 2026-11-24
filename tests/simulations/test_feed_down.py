"""Simulation 1: a feed outage is detected, labelled as a data outage, kept out of the metrics,
and the pipeline recovers by itself."""

import pytest

from .world import World, at, minute_range

SLO_AS_OF = "2026-11-22T16:59:30Z"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> World:
    world = World(tmp_path_factory.mktemp("feed_down"))
    world.feed_down(minute_range(at(14, 20), 5))  # 5 minutes: a warning, not an outage
    world.feed_down(
        minute_range(at(15, 0), 10)
    )  # 10 minutes: freshness fails, the hour is an outage
    world.curate()
    world.result = world.build(slo_as_of=SLO_AS_OF)  # type: ignore[attr-defined]
    return world


def test_every_feed_is_recorded_as_an_error_not_missing(built: World) -> None:
    runs = built.result.rows(  # type: ignore[attr-defined]
        "select * from agg_pipeline_health where scheduled_at >= timestamptz '2026-11-22 15:00:00+00' "
        "and scheduled_at < timestamptz '2026-11-22 15:10:00+00' order by scheduled_at"
    )
    assert len(runs) == 10
    for run in runs:
        assert {
            run["trip_updates_status"],
            run["vehicle_positions_status"],
            run["alerts_status"],
        } == {"error"}


def test_freshness_warns_then_fails_as_the_gap_grows(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    assert result.check("feed_gap_min", 9) == {"value": 5.0, "status": "warn"}
    assert result.check("feed_gap_min", 10) == {"value": 10.0, "status": "fail"}


def test_only_the_long_outage_makes_an_hour_a_data_outage(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    assert result.flags(9)["data_outage"] is False  # 55 minutes captured is still a full window
    assert result.flags(10)["data_outage"] is True
    assert result.flags(10)["vehicle_window_complete"] is False


def test_outage_hours_are_labelled_and_excluded_from_the_metrics(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    rows = result.rows(
        "select hour_local, data_outage, sum(n_arrivals) as used, sum(n_excluded_outage) as excluded, "
        "count(coverage_pct) as with_coverage from agg_actual_deviation_hourly "
        "group by all order by hour_local"
    )
    by_hour = {r["hour_local"]: r for r in rows}
    assert by_hour[10]["data_outage"] is True
    assert by_hour[10]["used"] == 0 and by_hour[10]["with_coverage"] == 0
    assert by_hour[10]["excluded"] > 0  # arrivals existed and were counted out, not hidden
    assert by_hour[9]["data_outage"] is False and by_hour[9]["used"] > 0
    slo = result.rows(
        "select * from agg_slo_daily where slo_name = 'arrival_coverage' and service_date = date '2026-11-22'"
    )
    assert all(r["total_events"] > 0 for r in slo)  # the outage hour is not in the denominator


def test_capture_completeness_spends_budget_on_the_failed_minutes(built: World) -> None:
    row = built.result.one(  # type: ignore[attr-defined]
        "select * from agg_slo_daily where slo_name = 'capture_completeness' and service_date = date '2026-11-22'"
    )
    assert row["total_events"] - row["good_events"] == 15 and row["met"] is False
    assert row["error_budget_used"] > 1


def test_the_pipeline_recovers_on_its_own(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    since = "scheduled_at >= timestamptz '2026-11-22 15:10:00+00'"
    runs = result.one(f"select count(*) as n from agg_pipeline_health where {since}")  # noqa: S608
    ok = result.one(
        f"select count(*) as n from agg_pipeline_health where {since} "  # noqa: S608
        "and trip_updates_status = 'ok' and vehicle_positions_status = 'ok' and alerts_status = 'ok'"
    )
    assert runs["n"] == ok["n"] == 110  # every run after the outage captured all three feeds
    assert result.check("feed_gap_min", 11) == {"value": 0.0, "status": "ok"}
    assert result.flags(11)["data_outage"] is False
    recovered = result.one(
        "select count(*) filter (where not data_outage) as clean from agg_actual_deviation_hourly "
        "where hour_local = 11"
    )
    assert recovered["clean"] > 0
