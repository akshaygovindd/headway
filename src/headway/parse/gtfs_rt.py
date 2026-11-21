"""Parse GTFS-realtime protobuf feeds into flat rows."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

Row = dict[str, Any]


class ParseError(Exception):
    """Raised when feed bytes cannot be decoded into a usable feed."""


@dataclass(frozen=True)
class CaptureMeta:
    capture_id: str
    fetched_at: datetime


def _ts(value: int) -> datetime | None:
    """Epoch seconds to aware UTC; 0 (unset) becomes None."""
    if value <= 0:
        return None
    try:
        return _EPOCH + timedelta(seconds=value)
    except OverflowError as exc:
        raise ParseError(f"timestamp out of representable range: {value}") from exc


def _decode(data: bytes) -> Any:
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(data)
    except DecodeError as exc:
        raise ParseError(f"protobuf decode failed: {exc}") from exc
    if not feed.HasField("header"):
        raise ParseError("feed has no header")
    return feed


def _header_ts(feed: Any) -> datetime:
    ts = _ts(feed.header.timestamp)
    if ts is None:
        raise ParseError("feed header has no timestamp")
    return ts


def parse_header(data: bytes) -> tuple[datetime, int, str]:
    """Return (feed_header_ts, entity_count, gtfs_realtime_version)."""
    feed = _decode(data)
    return _header_ts(feed), len(feed.entity), str(feed.header.gtfs_realtime_version)


def _opt(msg: Any, field: str) -> Any:
    return getattr(msg, field) if msg.HasField(field) else None


def _or_none(value: str) -> str | None:
    return value or None


def parse_trip_updates(data: bytes, meta: CaptureMeta) -> list[Row]:
    feed = _decode(data)
    header_ts = _header_ts(feed)
    rel = gtfs_realtime_pb2.TripUpdate.StopTimeUpdate.ScheduleRelationship
    rows: list[Row] = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip = tu.trip
        direction = _opt(trip, "direction_id")
        for stu in tu.stop_time_update:
            arrival = stu.arrival if stu.HasField("arrival") else None
            departure = stu.departure if stu.HasField("departure") else None
            unc_src = next(
                (t for t in (arrival, departure) if t is not None and t.HasField("uncertainty")),
                None,
            )
            rows.append(
                {
                    "capture_id": meta.capture_id,
                    "trip_id": trip.trip_id,
                    "route_id": _or_none(trip.route_id),
                    "direction_id": direction,
                    "start_date": _or_none(trip.start_date),
                    "start_time": _or_none(trip.start_time),
                    "vehicle_id": _or_none(tu.vehicle.id),
                    "stop_id": stu.stop_id,
                    "stop_sequence": _opt(stu, "stop_sequence"),
                    "predicted_arrival_ts": _ts(arrival.time) if arrival else None,
                    "predicted_departure_ts": _ts(departure.time) if departure else None,
                    "uncertainty": unc_src.uncertainty if unc_src else None,
                    "schedule_relationship": rel.Name(stu.schedule_relationship),
                    "event_ts": _ts(tu.timestamp),
                    "feed_header_ts": header_ts,
                    "fetched_at": meta.fetched_at,
                }
            )
    return rows


def parse_vehicle_positions(data: bytes, meta: CaptureMeta) -> list[Row]:
    feed = _decode(data)
    header_ts = _header_ts(feed)
    status = gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus
    rows: list[Row] = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        pos = v.position if v.HasField("position") else None
        rows.append(
            {
                "capture_id": meta.capture_id,
                "vehicle_id": _or_none(v.vehicle.id),
                "trip_id": _or_none(v.trip.trip_id),
                "route_id": _or_none(v.trip.route_id),
                "direction_id": _opt(v.trip, "direction_id"),
                "lat": pos.latitude if pos else None,
                "lon": pos.longitude if pos else None,
                "bearing": _opt(pos, "bearing") if pos else None,
                "speed": _opt(pos, "speed") if pos else None,
                "current_status": status.Name(v.current_status)
                if v.HasField("current_status")
                else None,
                "stop_id": _or_none(v.stop_id),
                "current_stop_sequence": _opt(v, "current_stop_sequence"),
                "event_ts": _ts(v.timestamp),
                "feed_header_ts": header_ts,
                "fetched_at": meta.fetched_at,
            }
        )
    return rows


def parse_alerts(data: bytes, meta: CaptureMeta) -> list[Row]:
    feed = _decode(data)
    header_ts = _header_ts(feed)
    alert_t = gtfs_realtime_pb2.Alert
    rows: list[Row] = []
    for entity in feed.entity:
        if not entity.HasField("alert"):
            continue
        alert = entity.alert
        period = alert.active_period[0] if alert.active_period else None
        header_text = (
            alert.header_text.translation[0].text if alert.header_text.translation else None
        )
        base: Row = {
            "capture_id": meta.capture_id,
            "alert_id": entity.id,
            "effect": alert_t.Effect.Name(alert.effect),
            "cause": alert_t.Cause.Name(alert.cause),
            "active_start": _ts(period.start) if period else None,
            "active_end": _ts(period.end) if period else None,
            "header_text": header_text,
            "feed_header_ts": header_ts,
            "fetched_at": meta.fetched_at,
        }
        informed = list(alert.informed_entity) or [None]
        for index, ie in enumerate(informed):
            rows.append(
                {
                    **base,
                    "entity_index": index,
                    "route_id": _or_none(ie.route_id) if ie else None,
                    "stop_id": _or_none(ie.stop_id) if ie else None,
                }
            )
    return rows
