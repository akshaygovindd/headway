from datetime import timedelta

from google.transit import gtfs_realtime_pb2 as pb

from fixtures import gtfs_rt as fx
from fixtures.rows import FETCHED_AT, HEADER_TS
from headway.config import load_city_config
from headway.ingest.live import build_live_snapshot, live_snapshot_key
from headway.parse.gtfs_rt import CaptureMeta

CONFIG = load_city_config("boston")
META = CaptureMeta("cap-1", FETCHED_AT)


def _feed(*vehicles: tuple[str, str, float, float, int]) -> bytes:
    """Each vehicle: (id, route_id, lat, lon, age_seconds)."""
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = fx.epoch(HEADER_TS)
    for vid, route, lat, lon, age in vehicles:
        v = feed.entity.add()
        v.id = vid
        v.vehicle.vehicle.id = vid
        v.vehicle.trip.trip_id = f"t-{vid}"
        v.vehicle.trip.route_id = route
        v.vehicle.position.latitude = lat
        v.vehicle.position.longitude = lon
        v.vehicle.timestamp = fx.epoch(HEADER_TS - timedelta(seconds=age))
    return feed.SerializeToString()


def test_snapshot_shape() -> None:
    snap = build_live_snapshot(fx.vehicles_feed(), META, CONFIG)
    assert set(snap) == {
        "generated_at",
        "feed_header_ts",
        "vehicle_count",
        "dropped_invalid",
        "vehicles",
    }
    assert snap["vehicle_count"] == 1
    assert snap["dropped_invalid"] == 0
    assert set(snap["vehicles"][0]) == {
        "vehicle_id",
        "route_id",
        "trip_id",
        "direction_id",
        "lat",
        "lon",
        "bearing",
        "current_status",
        "stop_id",
        "event_ts",
        "position_age_s",
        "is_stale_position",
    }
    assert snap["vehicles"][0]["position_age_s"] == 20
    assert snap["vehicles"][0]["is_stale_position"] is False


def test_only_subway_lines_kept() -> None:
    data = _feed(("a", "Red", 42.35, -71.06, 5), ("b", "1", 42.35, -71.06, 5))
    snap = build_live_snapshot(data, META, CONFIG)
    assert [v["vehicle_id"] for v in snap["vehicles"]] == ["a"]


def test_invalid_coordinates_dropped_and_counted() -> None:
    data = _feed(("a", "Red", 42.35, -71.06, 5), ("b", "Red", 10.0, -71.06, 5))
    snap = build_live_snapshot(data, META, CONFIG)
    assert snap["vehicle_count"] == 1
    assert snap["dropped_invalid"] == 1


def test_stale_position_kept_and_flagged() -> None:
    data = _feed(("a", "Orange", 42.35, -71.06, 11 * 60), ("b", "Orange", 42.36, -71.06, 600))
    snap = build_live_snapshot(data, META, CONFIG)
    flags = {v["vehicle_id"]: v["is_stale_position"] for v in snap["vehicles"]}
    assert flags == {"a": True, "b": False}
    assert snap["vehicles"][0]["position_age_s"] == 660


def test_empty_feed_gives_empty_snapshot() -> None:
    snap = build_live_snapshot(_feed(), META, CONFIG)
    assert snap["vehicles"] == []
    assert snap["feed_header_ts"].startswith("2026-06-01")


def test_snapshot_key() -> None:
    assert live_snapshot_key(CONFIG) == "public/boston/live/vehicles_latest.json"
