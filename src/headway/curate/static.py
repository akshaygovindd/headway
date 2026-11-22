"""Load GTFS static tables into the curated layer, one partition per feed_version."""

import io
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

from headway.config import CityConfig
from headway.curate.schemas import STATIC_SCHEMAS
from headway.parse.gtfs_scope import route_ids_of_types, with_parent_stations
from headway.parse.gtfs_static import (
    Calendar,
    CalendarDate,
    FeedInfo,
    GtfsParseError,
    GtfsSource,
    Route,
)
from headway.storage import Storage

logger = logging.getLogger(__name__)

# feed_info is written last: its presence marks a feed_version as completely loaded.
_SMALL_TABLES: list[tuple[str, Any]] = [
    ("routes", Route),
    ("calendar", Calendar),
    ("calendar_dates", CalendarDate),
    ("feed_info", FeedInfo),
]
_BLOCK_SIZE = 16 << 20


@dataclass
class StaticResult:
    feed_version: str
    skipped: bool
    rows: dict[str, int] = field(default_factory=dict)


def version_token(feed_version: str) -> str:
    """Path-safe form of a feed_version (the raw value stays in the feed_version column)."""
    return re.sub(r"[^A-Za-z0-9._=-]+", "_", feed_version).strip("_")


def static_key(prefix: str, table: str, feed_version: str) -> str:
    token = version_token(feed_version)
    return f"curated/{prefix}/static/table={table}/feed_version={token}/part.parquet"


def _feed_version(source: GtfsSource) -> str:
    for info in source.feed_info():
        if info.feed_version:
            return info.feed_version
    raise ValueError("GTFS feed_info.txt has no feed_version")


def _read_filtered(
    source: GtfsSource,
    table: str,
    keep: Callable[[pa.RecordBatch], pa.Array | None] | None,
    *,
    optional: bool = False,
) -> tuple[pa.Table, int]:
    """Stream a table with pyarrow.csv, typed by its curated schema; return (kept, rows read).

    ``keep`` maps a batch to a boolean mask; rows failing it are dropped before any other work.
    """
    schema = STATIC_SCHEMAS[table]
    fields = [f.with_nullable(True) for f in schema if f.name != "feed_version"]
    if not source.has_table(table):
        if optional:
            return pa.Table.from_batches([], schema=pa.schema(fields)), 0
        raise GtfsParseError(f"missing required table {table}.txt")
    convert = pacsv.ConvertOptions(
        column_types={f.name: f.type for f in fields},
        include_columns=[f.name for f in fields],
        include_missing_columns=True,
        null_values=[""],
        strings_can_be_null=True,
    )
    read = pacsv.ReadOptions(block_size=_BLOCK_SIZE, encoding="utf8")
    kept: list[pa.RecordBatch] = []
    total = 0
    try:
        with source.open_binary(table) as stream:
            for batch in pacsv.open_csv(stream, read_options=read, convert_options=convert):
                total += batch.num_rows
                if keep is not None:
                    mask = keep(batch)
                    if mask is not None:
                        batch = batch.filter(mask)
                if batch.num_rows:
                    kept.append(batch)
    except pa.ArrowInvalid as exc:
        raise GtfsParseError(f"{table}.txt: {exc}") from exc
    result = pa.Table.from_batches(kept, schema=pa.schema(fields))
    for f in schema:
        if f.name != "feed_version" and not f.nullable and result.column(f.name).null_count:
            raise GtfsParseError(f"{table}.txt: null value in required column {f.name}")
    return result, total


def _with_version(table: pa.Table, name: str, version: str) -> pa.Table:
    schema = STATIC_SCHEMAS[name]
    columns = [
        pa.repeat(version, table.num_rows) if f.name == "feed_version" else table.column(f.name)
        for f in schema
    ]
    return pa.Table.from_arrays(columns, schema=schema)


def _in(column: str, values: pa.Array) -> Callable[[pa.RecordBatch], pa.Array]:
    return lambda batch: pc.is_in(batch.column(column), value_set=values)


def load_static(storage: Storage, config: CityConfig, gtfs_source: GtfsSource) -> StaticResult:
    """Write static tables for the source's feed_version; no-op if it was already loaded.

    Scope: routes, calendar, calendar_dates and feed_info are kept whole; trips are limited to
    routes whose route_type is in ``config.route_types``; stop_times and shapes to those trips;
    stops to the ones those stop_times reference plus their parent stations.
    """
    version = _feed_version(gtfs_source)
    prefix = config.storage_prefix
    if storage.exists(static_key(prefix, "feed_info", version)):
        return StaticResult(feed_version=version, skipped=True)
    result = StaticResult(feed_version=version, skipped=False)

    def emit(name: str, table: pa.Table, rows_read: int, started: float) -> None:
        buf = io.BytesIO()
        pq.write_table(_with_version(table, name, version), buf, compression="zstd")
        storage.put_bytes(static_key(prefix, name, version), buf.getvalue())
        result.rows[name] = table.num_rows
        logger.info(
            json.dumps(
                {
                    "table": name,
                    "rows_read": rows_read,
                    "rows_kept": table.num_rows,
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
        )

    def small(name: str, model: Any) -> tuple[pa.Table, int, float]:
        started = time.monotonic()
        rows = (
            [r.model_dump() for r in gtfs_source.iter_rows(model)]
            if gtfs_source.has_table(name)
            else []
        )
        fields = [f for f in STATIC_SCHEMAS[name] if f.name != "feed_version"]
        return pa.Table.from_pylist(rows, schema=pa.schema(fields)), len(rows), started

    routes, routes_read, started = small("routes", Route)
    emit("routes", routes, routes_read, started)
    keep_routes = route_ids_of_types(
        zip(
            routes.column("route_id").to_pylist(),
            routes.column("route_type").to_pylist(),
            strict=True,
        ),
        config.route_types,
    )

    started = time.monotonic()
    trips, read = _read_filtered(
        gtfs_source, "trips", _in("route_id", pa.array(sorted(keep_routes), pa.string()))
    )
    emit("trips", trips, read, started)
    trip_ids = pc.unique(trips.column("trip_id"))
    shape_ids = pc.drop_null(pc.unique(trips.column("shape_id")))

    started = time.monotonic()
    stop_times, read = _read_filtered(gtfs_source, "stop_times", _in("trip_id", trip_ids))
    emit("stop_times", stop_times, read, started)

    started = time.monotonic()
    shapes, read = _read_filtered(gtfs_source, "shapes", _in("shape_id", shape_ids), optional=True)
    emit("shapes", shapes, read, started)

    started = time.monotonic()
    all_stops, read = _read_filtered(gtfs_source, "stops", None)
    parent_of = dict(
        zip(
            all_stops.column("stop_id").to_pylist(),
            all_stops.column("parent_station").to_pylist(),
            strict=True,
        )
    )
    needed = with_parent_stations(pc.unique(stop_times.column("stop_id")).to_pylist(), parent_of)
    stops = all_stops.filter(
        pc.is_in(all_stops.column("stop_id"), pa.array(sorted(needed), pa.string()))
    )
    emit("stops", stops, read, started)

    for name, model in _SMALL_TABLES[1:]:  # feed_info last: it marks the version as loaded
        table, read, started = small(name, model)
        emit(name, table, read, started)
    return result


def _latest_token(storage: Storage, config: CityConfig) -> str | None:
    """Partition token of the loaded feed_version with the latest feed_start_date."""
    pattern = re.compile(r"/feed_version=([^/]+)/part\.parquet$")
    prefix = f"curated/{config.storage_prefix}/static/table=feed_info/"
    best: tuple[str, str] | None = None
    for key in storage.list_keys(prefix):
        m = pattern.search(key)
        if m is None:
            continue
        info = pq.read_table(io.BytesIO(storage.get_bytes(key)), schema=STATIC_SCHEMAS["feed_info"])
        starts = [d for d in info.column("feed_start_date").to_pylist() if d is not None]
        cand = (max(starts).isoformat() if starts else "", m.group(1))
        best = cand if best is None or cand > best else best
    return best[1] if best else None


def load_dim_routes(storage: Storage, config: CityConfig) -> dict[str, int]:
    """Every route_id of the latest loaded feed_version mapped to its route_type."""
    token = _latest_token(storage, config)
    if token is None:
        raise LookupError("no static GTFS loaded; run load_static first")
    key = f"curated/{config.storage_prefix}/static/table=routes/feed_version={token}/part.parquet"
    table = pq.read_table(io.BytesIO(storage.get_bytes(key)), schema=STATIC_SCHEMAS["routes"])
    ids = table.column("route_id").to_pylist()
    types = table.column("route_type").to_pylist()
    return dict(zip(ids, types, strict=True))
