"""Explicit pyarrow schemas for curated tables.

Realtime tables carry capture lineage (``capture_id``, ``run_id``, ``content_hash``) so that
duplicate captures can be detected downstream; curation itself never dedupes. Static tables carry
the ``feed_version`` they were loaded from.
"""

from collections.abc import Iterable, Mapping
from typing import Any

import pyarrow as pa

TS = pa.timestamp("us", tz="UTC")

_LINEAGE = [
    pa.field("capture_id", pa.string(), nullable=False),
    pa.field("run_id", pa.string(), nullable=False),
    pa.field("city_id", pa.string(), nullable=False),
    pa.field("content_hash", pa.string(), nullable=False),
    pa.field("fetched_at", TS, nullable=False),
    pa.field("feed_header_ts", TS, nullable=False),
]

REALTIME_SCHEMAS: dict[str, pa.Schema] = {
    "trip_updates": pa.schema(
        [
            *_LINEAGE,
            pa.field("trip_id", pa.string(), nullable=False),
            pa.field("route_id", pa.string()),
            pa.field("direction_id", pa.int32()),
            pa.field("start_date", pa.date32()),
            pa.field("start_time", pa.string()),
            pa.field("vehicle_id", pa.string()),
            pa.field("stop_id", pa.string(), nullable=False),
            pa.field("stop_sequence", pa.int32()),
            pa.field("predicted_arrival_ts", TS),
            pa.field("predicted_departure_ts", TS),
            pa.field("uncertainty", pa.int32()),
            pa.field("schedule_relationship", pa.string()),
            pa.field("event_ts", TS),
        ]
    ),
    "vehicle_positions": pa.schema(
        [
            *_LINEAGE,
            pa.field("vehicle_id", pa.string(), nullable=False),
            pa.field("trip_id", pa.string()),
            pa.field("route_id", pa.string()),
            pa.field("direction_id", pa.int32()),
            pa.field("lat", pa.float64(), nullable=False),
            pa.field("lon", pa.float64(), nullable=False),
            pa.field("bearing", pa.float64()),
            pa.field("speed", pa.float64()),
            pa.field("current_status", pa.string()),
            pa.field("stop_id", pa.string()),
            pa.field("current_stop_sequence", pa.int32()),
            pa.field("event_ts", TS),
            pa.field("is_stale_position", pa.bool_(), nullable=False),
        ]
    ),
    "alerts": pa.schema(
        [
            *_LINEAGE,
            pa.field("alert_id", pa.string(), nullable=False),
            pa.field("entity_index", pa.int32(), nullable=False),
            pa.field("effect", pa.string()),
            pa.field("cause", pa.string()),
            pa.field("route_id", pa.string()),
            pa.field("stop_id", pa.string()),
            pa.field("active_start", TS),
            pa.field("active_end", TS),
            pa.field("header_text", pa.string()),
        ]
    ),
}

# Curated copies of ingest bookkeeping, so the transform reads a few Parquet files instead of
# one JSON object per ingest run (manifests) or per capture (quarantine). The raw JSON objects
# stay the source of truth; these are rebuilt from them by the backfill task.
META_SCHEMAS: dict[str, pa.Schema] = {
    # One row per manifest entry (a capture attempt). Explicit TIMESTAMPTZ types so every DuckDB
    # version reads the same schema (JSON inference differs between 1.5 and 2.0).
    "manifests": pa.schema(
        [
            pa.field("run_id", pa.string(), nullable=False),
            pa.field("capture_id", pa.string(), nullable=False),
            pa.field("city_id", pa.string(), nullable=False),
            pa.field("feed", pa.string(), nullable=False),
            pa.field("status", pa.string(), nullable=False),
            pa.field("http_status", pa.int64()),
            pa.field("scheduled_at", TS, nullable=False),
            pa.field("fetched_at", TS, nullable=False),
            pa.field("poll_index", pa.int32()),  # null in parts written before 15 s sampling
            pa.field("feed_header_ts", TS),
            pa.field("entity_count", pa.int64()),
            pa.field("bytes", pa.int64()),
            pa.field("content_hash", pa.string()),
            pa.field("s3_key", pa.string()),
            pa.field("error", pa.string()),
            pa.field("header_parse_error", pa.string()),
        ]
    ),
    # One row per quarantined record (or whole capture for parse errors). ``source_table`` is the
    # feed table the record belonged to; ``line_no`` is its position in the JSONL object.
    "quarantine": pa.schema(
        [
            pa.field("capture_id", pa.string(), nullable=False),
            pa.field("source_table", pa.string(), nullable=False),
            pa.field("reason", pa.string(), nullable=False),
            pa.field("error", pa.string(), nullable=False),
            pa.field("line_no", pa.int32(), nullable=False),
            pa.field("fetched_at", TS),
        ]
    ),
}

# Every table that is written as hourly part files and merged by compaction.
COMPACTABLE_SCHEMAS: dict[str, pa.Schema] = {**REALTIME_SCHEMAS, **META_SCHEMAS}

_FV = pa.field("feed_version", pa.string(), nullable=False)

STATIC_SCHEMAS: dict[str, pa.Schema] = {
    "routes": pa.schema(
        [
            _FV,
            pa.field("route_id", pa.string(), nullable=False),
            pa.field("agency_id", pa.string()),
            pa.field("route_short_name", pa.string()),
            pa.field("route_long_name", pa.string()),
            pa.field("route_desc", pa.string()),
            pa.field("route_type", pa.int32(), nullable=False),
            pa.field("route_url", pa.string()),
            pa.field("route_color", pa.string()),
            pa.field("route_text_color", pa.string()),
            pa.field("route_sort_order", pa.int32()),
            pa.field("line_id", pa.string()),
        ]
    ),
    "stops": pa.schema(
        [
            _FV,
            pa.field("stop_id", pa.string(), nullable=False),
            pa.field("stop_code", pa.string()),
            pa.field("stop_name", pa.string()),
            pa.field("stop_desc", pa.string()),
            pa.field("platform_code", pa.string()),
            pa.field("platform_name", pa.string()),
            pa.field("stop_lat", pa.float64()),
            pa.field("stop_lon", pa.float64()),
            pa.field("zone_id", pa.string()),
            pa.field("location_type", pa.int32()),
            pa.field("parent_station", pa.string()),
            pa.field("wheelchair_boarding", pa.int32()),
            pa.field("level_id", pa.string()),
        ]
    ),
    "trips": pa.schema(
        [
            _FV,
            pa.field("route_id", pa.string(), nullable=False),
            pa.field("service_id", pa.string(), nullable=False),
            pa.field("trip_id", pa.string(), nullable=False),
            pa.field("trip_headsign", pa.string()),
            pa.field("trip_short_name", pa.string()),
            pa.field("direction_id", pa.int32()),
            pa.field("block_id", pa.string()),
            pa.field("shape_id", pa.string()),
            pa.field("wheelchair_accessible", pa.int32()),
            pa.field("route_pattern_id", pa.string()),
        ]
    ),
    "stop_times": pa.schema(
        [
            _FV,
            pa.field("trip_id", pa.string(), nullable=False),
            pa.field("arrival_time", pa.string()),
            pa.field("departure_time", pa.string()),
            pa.field("stop_id", pa.string(), nullable=False),
            pa.field("stop_sequence", pa.int32(), nullable=False),
            pa.field("stop_headsign", pa.string()),
            pa.field("pickup_type", pa.int32()),
            pa.field("drop_off_type", pa.int32()),
            pa.field("timepoint", pa.int32()),
        ]
    ),
    "shapes": pa.schema(
        [
            _FV,
            pa.field("shape_id", pa.string(), nullable=False),
            pa.field("shape_pt_lat", pa.float64(), nullable=False),
            pa.field("shape_pt_lon", pa.float64(), nullable=False),
            pa.field("shape_pt_sequence", pa.int32(), nullable=False),
            pa.field("shape_dist_traveled", pa.float64()),
        ]
    ),
    # Precomputed at static load (headway.curate.geometry): distance along each shape, and each
    # stop's projection onto its shape. Metres from the shape's first point.
    "shape_points_dist": pa.schema(
        [
            _FV,
            pa.field("shape_id", pa.string(), nullable=False),
            pa.field("shape_pt_sequence", pa.int32(), nullable=False),
            pa.field("lat", pa.float64(), nullable=False),
            pa.field("lon", pa.float64(), nullable=False),
            pa.field("dist_m", pa.float64(), nullable=False),
        ]
    ),
    "stop_shape_positions": pa.schema(
        [
            _FV,
            pa.field("shape_id", pa.string(), nullable=False),
            pa.field("stop_id", pa.string(), nullable=False),
            pa.field("stop_sequence", pa.int32(), nullable=False),
            pa.field("dist_m", pa.float64(), nullable=False),
        ]
    ),
    "calendar": pa.schema(
        [
            _FV,
            pa.field("service_id", pa.string(), nullable=False),
            *[
                pa.field(d, pa.bool_(), nullable=False)
                for d in (
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                )
            ],
            pa.field("start_date", pa.date32(), nullable=False),
            pa.field("end_date", pa.date32(), nullable=False),
        ]
    ),
    "calendar_dates": pa.schema(
        [
            _FV,
            pa.field("service_id", pa.string(), nullable=False),
            pa.field("date", pa.date32(), nullable=False),
            pa.field("exception_type", pa.int32(), nullable=False),
            pa.field("holiday_name", pa.string()),
        ]
    ),
    "feed_info": pa.schema(
        [
            _FV,
            pa.field("feed_publisher_name", pa.string(), nullable=False),
            pa.field("feed_publisher_url", pa.string(), nullable=False),
            pa.field("feed_lang", pa.string(), nullable=False),
            pa.field("feed_start_date", pa.date32()),
            pa.field("feed_end_date", pa.date32()),
            pa.field("feed_contact_email", pa.string()),
        ]
    ),
}

# Daily weather (Release 3, ADR-022): one row per local date, one part per date. A flag is null when
# the value it needs is missing; such a day is left out of that comparison.
WEATHER_SCHEMA = pa.schema(
    [
        pa.field("date", pa.date32(), nullable=False),
        pa.field("snow_flag", pa.bool_()),
        pa.field("heavy_rain_flag", pa.bool_()),
        pa.field("extreme_heat_flag", pa.bool_()),
        pa.field("snowfall_mm", pa.float64()),
        pa.field("precip_mm", pa.float64()),
        pa.field("tmax_c", pa.float64()),
        pa.field("source", pa.string(), nullable=False),
    ]
)

SCHEMAS: dict[str, pa.Schema] = {**REALTIME_SCHEMAS, **STATIC_SCHEMAS}


def to_table(name: str, rows: Iterable[Mapping[str, Any]]) -> pa.Table:
    """Build a table with the explicit schema; missing optional columns become null."""
    schema = SCHEMAS.get(name) or META_SCHEMAS[name]
    materialized = list(rows)
    columns = {f.name: [r.get(f.name) for r in materialized] for f in schema}
    return pa.Table.from_pydict(columns, schema=schema)
