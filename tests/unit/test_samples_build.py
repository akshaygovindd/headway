import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as pb

from headway.config import load_city_config
from headway.ingest.capture import capture_run
from headway.ingest.fetch import FetchResult
from headway.parse.gtfs_static import GtfsSource
from headway.samples.build import build_sample
from headway.samples.faults import parse_fault_counts
from headway.samples.filter import filter_feed
from headway.storage import LocalStorage

START = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)  # a Sunday morning in Boston
RUNS = 40


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _feed(ts: datetime) -> pb.FeedMessage:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = _epoch(ts)
    return feed


def _trip_updates(ts: datetime) -> bytes:
    feed = _feed(ts)
    for i, route in enumerate(["Red", "Red", "Red", "Bus1", "Bus1"]):
        e = feed.entity.add()
        e.id = f"tu{i}"
        tu = e.trip_update
        tu.trip.trip_id = f"trip{i}"
        tu.trip.route_id = route
        tu.timestamp = _epoch(ts)
        for seq in (1, 2):
            stu = tu.stop_time_update.add()
            stu.stop_id = f"s{seq}"
            stu.stop_sequence = seq
            stu.arrival.time = _epoch(ts + timedelta(minutes=5 * seq))
    return feed.SerializeToString()


def _vehicles(ts: datetime) -> bytes:
    feed = _feed(ts)
    for i, route in enumerate(["Red", "Red", "Red", "Bus1", "Bus1"]):
        e = feed.entity.add()
        e.id = f"v{i}"
        v = e.vehicle
        v.vehicle.id = f"veh{i}"
        v.trip.trip_id = f"trip{i}"
        v.trip.route_id = route
        v.position.latitude = 42.35
        v.position.longitude = -71.06
        v.timestamp = _epoch(ts - timedelta(seconds=10))
    return feed.SerializeToString()


def _alerts(ts: datetime) -> bytes:
    feed = _feed(ts)
    for name, routes in [("a-red", ["Red"]), ("a-bus", ["Bus1"]), ("a-mix", ["Bus1", "Red"])]:
        e = feed.entity.add()
        e.id = name
        e.alert.effect = pb.Alert.DETOUR
        for r in routes:
            e.alert.informed_entity.add().route_id = r
    return feed.SerializeToString()


def _make_capture(root: Path) -> None:
    config = load_city_config("boston")
    for n in range(RUNS):
        at = START + timedelta(minutes=n)
        bodies = {
            str(config.feeds.trip_updates): _trip_updates(at),
            str(config.feeds.vehicle_positions): _vehicles(at),
            str(config.feeds.alerts): _alerts(at),
        }

        def fetch(url: str, at: datetime = at, bodies: dict[str, bytes] = bodies) -> FetchResult:
            return FetchResult("ok", 200, bodies[url], at + timedelta(seconds=1), None)

        capture_run(config, LocalStorage(root), fetch, lambda at=at: at, at, offsets=(0,))  # type: ignore[misc]


GTFS = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n1,MBTA,http://x,America/New_York\n",
    "routes.txt": "route_id,route_type\nRed,1\nBus1,3\n",
    "stops.txt": "stop_id,stop_name,location_type,parent_station\n"
    "place-a,A,1,\ns1,A,0,place-a\ns2,B,0,\ns3,C,0,\n",
    "trips.txt": "route_id,service_id,trip_id,shape_id\n"
    "Red,sun,t1,sh1\nRed,wk,t2,sh2\nBus1,sun,t3,sh3\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,09:00:00,09:00:00,s1,1\nt1,09:05:00,09:05:00,s2,2\n"
    "t2,09:00:00,09:00:00,s3,1\nt3,09:00:00,09:00:00,s3,1\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
    "sh1,42.1,-71.1,1\nsh2,42.2,-71.2,1\nsh3,42.3,-71.3,1\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\nsun,0,0,0,0,0,0,1,20261101,20261231\n"
    "wk,1,1,1,1,1,0,0,20261101,20261231\n",
    "calendar_dates.txt": "service_id,date,exception_type\nsun,20261125,1\nwk,20261126,2\n",
    "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang\nMBTA,http://x,en\n",
}


def _make_gtfs(folder: Path) -> None:
    folder.mkdir()
    for name, text in GTFS.items():
        (folder / name).write_text(text)


def _hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _entity_ids(path: Path) -> list[str]:
    feed = pb.FeedMessage()
    feed.ParseFromString(gzip.decompress(path.read_bytes()))
    return [e.id for e in feed.entity]


def _build(tmp_path: Path, name: str = "out") -> Path:
    src, gtfs = tmp_path / "src", tmp_path / "gtfs"
    if not src.exists():
        _make_capture(src)
        _make_gtfs(gtfs)
    out = tmp_path / name
    build_sample(load_city_config("boston"), src, gtfs, out)
    return out


def test_filter_feed_keeps_only_requested_routes() -> None:
    routes = frozenset({"Red"})
    tu = pb.FeedMessage.FromString(filter_feed("trip_updates", _trip_updates(START), routes))
    assert [e.id for e in tu.entity] == ["tu0", "tu1", "tu2"]
    assert tu.header.timestamp == _epoch(START)
    al = pb.FeedMessage.FromString(filter_feed("alerts", _alerts(START), routes))
    assert [e.id for e in al.entity] == ["a-red", "a-mix"]
    assert filter_feed("alerts", b"\xff\xff garbage", routes) == b"\xff\xff garbage"


def test_build_filters_and_subsets_gtfs(tmp_path: Path) -> None:
    out = _build(tmp_path)
    for path in (out / "raw").rglob("vehicle_positions-*.pb.gz"):
        assert set(_entity_ids(path)) <= {"v0", "v1", "v2"}
    gtfs = GtfsSource(out / "gtfs")
    assert [t.trip_id for t in gtfs.trips()] == ["t1"]
    assert [s.stop_id for s in gtfs.stops()] == ["place-a", "s1", "s2"]
    assert [s.trip_id for s in gtfs.stop_times()] == ["t1", "t1"]
    assert {p.shape_id for p in gtfs.shapes()} == {"sh1"}
    assert [c.service_id for c in gtfs.calendar()] == ["sun"]
    # routes.txt keeps every route (production-like); other tables are subway only.
    assert [r.route_id for r in gtfs.routes()] == ["Bus1", "Red"]
    assert [c.service_id for c in gtfs.calendar_dates()] == ["sun"]


def test_seeded_faults_and_expected_counts(tmp_path: Path) -> None:
    out = _build(tmp_path)
    counts = parse_fault_counts((out / "FAULTS.md").read_text())
    assert counts == {
        "corrupt_trip_updates": 1,
        "duplicate_capture": 2,
        "missing_minute": 1,
        "trip_id_cleared": 2,
        "vehicle_out_of_bbox": 3,
    }
    expected = json.loads((out / "EXPECTED.json").read_text())
    assert expected["captures_per_feed"] == {
        "alerts": RUNS - 1,
        "trip_updates": RUNS - 1,
        "vehicle_positions": RUNS - 1,
    }
    assert expected["header_parse_failures"] == 1
    assert expected["unparseable_captures_per_feed"] == {"trip_updates": 1}
    q = expected["quarantine_by_reason"]
    assert q["vehicle_positions"] == {"invalid_coordinates": 3}
    assert q["trip_updates"]["missing_trip_id"] == 4  # two entities, two stop updates each
    assert q["trip_updates"]["protobuf_parse_error"] == 1
    manifests = list((out / "manifests").rglob("*.json"))
    assert len(manifests) == RUNS - 1
    hashes = [
        e["content_hash"]
        for m in sorted(manifests)
        for e in json.loads(m.read_text())["entries"]
        if e["feed"] == "trip_updates"
    ]
    assert len(hashes) - len(set(hashes)) == 2  # the two duplicated captures


def test_build_is_deterministic(tmp_path: Path) -> None:
    first = _hashes(_build(tmp_path, "one"))
    second = _hashes(_build(tmp_path, "two"))
    assert first == second and len(first) > 10
