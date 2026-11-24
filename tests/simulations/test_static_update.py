"""Simulation 5: a new GTFS static feed version is loaded while the pipeline runs. Each service
date joins exactly one version (the latest that has started), so nothing is doubled or lost."""

import csv
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from headway.curate.static import version_token

from .world import SAMPLE, World

FEED_V2 = "Fall 2026, 2026-11-22T13:00:00+00:00, version E"
# dbt sees the path-safe token of a feed_version
V1 = version_token("Fall 2026, 2026-11-19T01:01:13+00:00, version D")
V2 = version_token(FEED_V2)
SLO_AS_OF = "2026-11-22T16:59:30Z"


def _second_version(tmp: Path) -> Path:
    """The sample feed again as a new version that takes effect on 2026-11-22."""
    target = tmp / "gtfs_v2"
    shutil.copytree(SAMPLE / "gtfs", target)
    with (target / "feed_info.txt").open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["feed_version"] = FEED_V2
    rows[0]["feed_start_date"] = "20261122"
    with (target / "feed_info.txt").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return target


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> World:
    tmp = tmp_path_factory.mktemp("static_update")
    world = World(tmp)
    # an hour on the previous service date (01:00-01:59 local on 11-22 still belongs to 11-21)
    world.extend(datetime(2026, 11, 22, 6, 0, tzinfo=UTC), 60)
    world.curate(extra_static=_second_version(tmp))
    world.result = world.build(slo_as_of=SLO_AS_OF)  # type: ignore[attr-defined]
    return world


def test_both_versions_are_loaded_and_the_new_one_takes_effect_on_its_start_date(
    built: World,
) -> None:
    result = built.result  # type: ignore[attr-defined]
    versions = result.rows(
        "select feed_version, feed_start_date::varchar as start from stg_gtfs__feed_info"
    )
    assert {v["feed_version"] for v in versions} == {V1, V2}
    mapped = {
        r["service_date"]: r["feed_version"]
        for r in result.rows(
            "select service_date::varchar as service_date, feed_version "
            "from int_feed_version_by_service_date"
        )
    }
    assert mapped == {"2026-11-21": V1, "2026-11-22": V2}


def test_each_service_date_schedules_from_one_version_without_doubling(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    rows = result.rows(
        "select service_date::varchar as service_date, count(distinct feed_version) as versions, "
        "min(feed_version) as version, count(*) as n, "
        "count(distinct (trip_instance_key, stop_sequence)) as distinct_trip_stops "
        "from int_scheduled_stop_times group by 1 order by 1"
    )
    by_date = {r["service_date"]: r for r in rows}
    for r in rows:
        assert r["versions"] == 1
        assert r["n"] == r["distinct_trip_stops"]  # no trip stop twice
    assert by_date["2026-11-22"]["version"] == V2


def test_schedule_join_coverage_holds_after_the_switch(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    rows = result.rows(
        "select hour_local, value from agg_quality_checks_hourly "
        "where check_name = 'schedule_join_coverage' and service_date = date '2026-11-22' "
        "order by hour_local"
    )
    assert rows, "no schedule join coverage was measured"
    assert all(r["value"] >= 90 for r in rows)
    status = {
        r["status"]
        for r in result.rows(
            "select status from agg_quality_checks_hourly where check_name = 'schedule_join_coverage'"
        )
    }
    assert status == {"ok"}


def test_a_new_version_is_not_an_outage_and_the_metrics_keep_flowing(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    for hour in (9, 10, 11):
        assert result.flags(hour)["data_outage"] is False
    used = result.one(
        "select sum(n_arrivals) as n from agg_actual_deviation_hourly where service_date = date '2026-11-22'"
    )
    assert used["n"] > 0
