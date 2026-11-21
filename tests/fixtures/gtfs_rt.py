"""Synthetic GTFS-realtime feed builders."""

from datetime import UTC, datetime, timedelta

from google.transit import gtfs_realtime_pb2 as pb

from fixtures.rows import FETCHED_AT, HEADER_TS


def epoch(dt: datetime) -> int:
    return int(dt.astimezone(UTC).timestamp())


def _feed(header_ts: datetime | None = HEADER_TS) -> pb.FeedMessage:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.incrementality = pb.FeedHeader.FULL_DATASET
    if header_ts is not None:
        feed.header.timestamp = epoch(header_ts)
    return feed


def trip_updates_feed(
    *,
    trip_id: str = "t1",
    stop_id: str = "70061",
    arrival: datetime | None = FETCHED_AT + timedelta(minutes=4),
    header_ts: datetime | None = HEADER_TS,
) -> bytes:
    feed = _feed(header_ts)
    e = feed.entity.add()
    e.id = "e1"
    tu = e.trip_update
    tu.trip.trip_id = trip_id
    tu.trip.route_id = "Red"
    tu.trip.direction_id = 0
    tu.trip.start_date = "20260601"
    tu.trip.start_time = "11:50:00"
    tu.vehicle.id = "y1"
    tu.timestamp = epoch(HEADER_TS)
    for seq in (3, 4):
        stu = tu.stop_time_update.add()
        stu.stop_id = stop_id
        stu.stop_sequence = seq
        if arrival is not None:
            stu.arrival.time = epoch(arrival + timedelta(minutes=seq))
            stu.arrival.uncertainty = 30
    return feed.SerializeToString()


def vehicles_feed(*, lat: float = 42.35, lon: float = -71.06, vehicle_id: str = "y1") -> bytes:
    feed = _feed()
    e = feed.entity.add()
    e.id = "v1"
    v = e.vehicle
    v.vehicle.id = vehicle_id
    v.trip.trip_id = "t1"
    v.trip.route_id = "Red"
    v.trip.direction_id = 1
    v.position.latitude = lat
    v.position.longitude = lon
    v.position.bearing = 90.0
    v.current_status = pb.VehiclePosition.IN_TRANSIT_TO
    v.stop_id = "70061"
    v.current_stop_sequence = 3
    v.timestamp = epoch(HEADER_TS - timedelta(seconds=20))
    return feed.SerializeToString()


def alerts_feed(*, informed: int = 2) -> bytes:
    feed = _feed()
    e = feed.entity.add()
    e.id = "a1"
    a = e.alert
    a.effect = pb.Alert.SIGNIFICANT_DELAYS
    a.cause = pb.Alert.CONSTRUCTION
    p = a.active_period.add()
    p.start = epoch(HEADER_TS)
    tr = a.header_text.translation.add()
    tr.text = "Delays on Red Line"
    tr.language = "en"
    for i in range(informed):
        ie = a.informed_entity.add()
        ie.route_id = "Red"
        if i:
            ie.stop_id = "70061"
    return feed.SerializeToString()


CORRUPT = b"\xff\xff\xff\xff\x0a\x99garbage"
