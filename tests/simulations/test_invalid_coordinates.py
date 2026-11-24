"""Simulation 4: a burst of vehicles with impossible coordinates is quarantined, not dropped,
and the quarantine share warns and then fails as the burst grows."""

import pytest
from google.transit import gtfs_realtime_pb2 as pb

from headway.ingest.fetch import FetchResult

from .world import CONFIG, World, at, minute_range

SLO_AS_OF = "2026-11-22T16:59:30Z"


def _bent(body: bytes, n: int) -> bytes:
    """The vehicle feed with its first n vehicles moved to (0, 0), off the map."""
    feed = pb.FeedMessage()
    feed.ParseFromString(body)
    for entity in list(feed.entity)[:n]:
        entity.vehicle.position.latitude = 0.0
        entity.vehicle.position.longitude = 0.0
    return feed.SerializeToString()


def _burst(world: World, minutes: list, n: int) -> None:
    def fetcher(minute):  # type: ignore[no-untyped-def]
        base = world.fetcher(minute)

        def fetch(url: str) -> FetchResult:
            result = base(url)
            if url == str(CONFIG.feeds.vehicle_positions) and result.body is not None:
                return FetchResult("ok", 200, _bent(result.body, n), result.fetched_at, None)
            return result

        return fetch

    world.drop(minutes)
    for minute in minutes:
        world.run(minute, fetcher(minute))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> World:
    world = World(tmp_path_factory.mktemp("invalid_coordinates"))
    _burst(world, [at(14, 40)], 50)  # hour 9: 50 vehicles in one capture
    _burst(world, minute_range(at(15, 40), 5), 50)  # hour 10: five captures, 250 vehicles
    world.curate()
    world.result = world.build(slo_as_of=SLO_AS_OF)  # type: ignore[attr-defined]
    return world


def test_the_bad_rows_are_kept_in_quarantine(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    rows = result.rows(
        "select c.hour_local, count(*) as n from stg_quarantine q "
        "join int_captures c on c.capture_id = q.capture_id "
        "where q.reason = 'invalid_coordinates' group by 1 order by 1"
    )
    by_hour = {r["hour_local"]: r["n"] for r in rows}
    assert by_hour[9] == 50
    assert by_hour[10] >= 250  # plus the three seeded out-of-bbox vehicles of the sample
    assert 11 not in by_hour


def test_the_valid_vehicles_of_those_captures_are_still_used(built: World) -> None:
    row = built.result.one(  # type: ignore[attr-defined]
        "select count(*) as n from stg_vehicle_positions "
        "where capture_id like 'vehicle_positions-20261122T144005Z-%'"
    )
    assert 0 < row["n"] < 50


def test_the_share_warns_for_a_small_burst_and_fails_for_a_large_one(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    nine, ten = (
        result.check("invalid_coordinate_share", 9),
        result.check("invalid_coordinate_share", 10),
    )
    assert nine["status"] == "warn" and 0.5 <= nine["value"] < 5
    assert ten["status"] == "fail" and ten["value"] >= 5


def test_the_burst_is_not_a_data_outage_and_the_next_hour_recovers(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    assert [result.flags(h)["data_outage"] for h in (9, 10, 11)] == [False, False, False]
    assert result.check("invalid_coordinate_share", 11)["status"] == "ok"
    assert result.check("invalid_coordinate_share", 11)["value"] == 0.0
