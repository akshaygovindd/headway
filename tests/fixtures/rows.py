"""Flat row builders for contract tests."""

from datetime import UTC, datetime, timedelta
from typing import Any

from headway.config import BBox

FETCHED_AT = datetime(2026, 6, 1, 12, 0, 5, tzinfo=UTC)
HEADER_TS = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
BBOX = BBox(lat_min=42.0, lat_max=42.6, lon_min=-71.5, lon_max=-70.8)


def trip_update_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "capture_id": "trip_updates-20260601T120005Z-abcd1234",
        "trip_id": "t1",
        "route_id": "Red",
        "direction_id": 0,
        "start_date": "20260601",
        "start_time": "11:50:00",
        "vehicle_id": "y1",
        "stop_id": "70061",
        "stop_sequence": 3,
        "predicted_arrival_ts": FETCHED_AT + timedelta(minutes=4),
        "predicted_departure_ts": None,
        "uncertainty": None,
        "schedule_relationship": "SCHEDULED",
        "event_ts": HEADER_TS,
        "feed_header_ts": HEADER_TS,
        "fetched_at": FETCHED_AT,
    }
    row.update(over)
    return row


def vehicle_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "capture_id": "vehicle_positions-20260601T120005Z-abcd1234",
        "vehicle_id": "y1",
        "trip_id": "t1",
        "route_id": "Red",
        "direction_id": 0,
        "lat": 42.35,
        "lon": -71.06,
        "bearing": 90.0,
        "speed": 5.0,
        "current_status": "IN_TRANSIT_TO",
        "stop_id": "70061",
        "current_stop_sequence": 3,
        "event_ts": HEADER_TS - timedelta(seconds=20),
        "feed_header_ts": HEADER_TS,
        "fetched_at": FETCHED_AT,
    }
    row.update(over)
    return row


def alert_row(**over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "capture_id": "alerts-20260601T120005Z-abcd1234",
        "alert_id": "a1",
        "entity_index": 0,
        "effect": "DELAY",
        "cause": "UNKNOWN_CAUSE",
        "route_id": "Red",
        "stop_id": None,
        "active_start": HEADER_TS,
        "active_end": None,
        "header_text": "Delays",
        "feed_header_ts": HEADER_TS,
        "fetched_at": FETCHED_AT,
    }
    row.update(over)
    return row
