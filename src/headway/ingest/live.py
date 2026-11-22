"""Build the public live vehicle snapshot from a raw vehicle positions feed."""

from typing import Any

from headway.config import CityConfig
from headway.contracts.quarantine import validate_records
from headway.contracts.records import VehiclePositionRecord
from headway.parse.gtfs_rt import CaptureMeta, parse_header, parse_vehicle_positions

LIVE_CONTENT_TYPE = "application/json"
LIVE_CACHE_CONTROL = "max-age=30"


def live_snapshot_key(config: CityConfig) -> str:
    return f"public/{config.storage_prefix}/live/vehicles_latest.json"


def build_live_snapshot(
    vehicle_positions: bytes, meta: CaptureMeta, config: CityConfig
) -> dict[str, Any]:
    """Return the JSON-serialisable snapshot of current subway vehicle positions.

    Only vehicles on the configured subway lines are kept. Rows that fail validation
    (for example invalid coordinates) are dropped and counted in ``dropped_invalid``.
    Stale positions are kept and flagged.
    """
    lines = set(config.lines)
    rows = [r for r in parse_vehicle_positions(vehicle_positions, meta) if r["route_id"] in lines]
    valid, quarantined = validate_records(rows, VehiclePositionRecord, {"bbox": config.bbox})

    vehicles: list[dict[str, Any]] = []
    for rec in sorted(valid, key=lambda r: r.vehicle_id):
        age = None if rec.event_ts is None else (rec.feed_header_ts - rec.event_ts).total_seconds()
        vehicles.append(
            {
                "vehicle_id": rec.vehicle_id,
                "route_id": rec.route_id,
                "trip_id": rec.trip_id,
                "direction_id": rec.direction_id,
                "lat": rec.lat,
                "lon": rec.lon,
                "bearing": rec.bearing,
                "current_status": rec.current_status,
                "stop_id": rec.stop_id,
                "event_ts": None if rec.event_ts is None else rec.event_ts.isoformat(),
                "position_age_s": age,
                "is_stale_position": rec.is_stale_position,
            }
        )

    header_ts = valid[0].feed_header_ts if valid else parse_header(vehicle_positions)[0]
    return {
        "generated_at": meta.fetched_at.isoformat(),
        "feed_header_ts": header_ts.isoformat(),
        "vehicle_count": len(vehicles),
        "dropped_invalid": len(quarantined),
        "vehicles": vehicles,
    }
