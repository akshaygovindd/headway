"""Subway filtering of raw GTFS-realtime captures."""

from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from headway.contracts.types import FeedName


def _keep(feed: FeedName, entity: gtfs_realtime_pb2.FeedEntity, routes: frozenset[str]) -> bool:
    if feed == "trip_updates":
        return entity.HasField("trip_update") and entity.trip_update.trip.route_id in routes
    if feed == "vehicle_positions":
        return entity.HasField("vehicle") and entity.vehicle.trip.route_id in routes
    return entity.HasField("alert") and any(
        ie.route_id in routes for ie in entity.alert.informed_entity
    )


def filter_feed(feed: FeedName, data: bytes, routes: frozenset[str]) -> bytes:
    """Keep entities on ``routes``; the original header is preserved.

    Bytes that do not decode are returned unchanged.
    """
    source = gtfs_realtime_pb2.FeedMessage()
    try:
        source.ParseFromString(data)
    except DecodeError:
        return data
    out = gtfs_realtime_pb2.FeedMessage()
    out.header.CopyFrom(source.header)
    out.entity.extend(e for e in source.entity if _keep(feed, e, routes))
    return out.SerializeToString(deterministic=True)
