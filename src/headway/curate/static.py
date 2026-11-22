"""Load GTFS static tables into the curated layer, one partition per feed_version."""

import io
import re
from dataclasses import dataclass, field
from typing import Any

import pyarrow.parquet as pq

from headway.config import CityConfig
from headway.curate.schemas import STATIC_SCHEMAS, to_table
from headway.parse.gtfs_static import (
    Calendar,
    CalendarDate,
    FeedInfo,
    GtfsSource,
    Route,
    ShapePoint,
    Stop,
    StopTime,
    Trip,
)
from headway.storage import Storage

# feed_info is written last: its presence marks a feed_version as completely loaded.
_TABLES: list[tuple[str, Any]] = [
    ("routes", Route),
    ("stops", Stop),
    ("trips", Trip),
    ("stop_times", StopTime),
    ("shapes", ShapePoint),
    ("calendar", Calendar),
    ("calendar_dates", CalendarDate),
    ("feed_info", FeedInfo),
]


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


def load_static(storage: Storage, config: CityConfig, gtfs_source: GtfsSource) -> StaticResult:
    """Write static tables for the source's feed_version; no-op if it was already loaded."""
    version = _feed_version(gtfs_source)
    prefix = config.storage_prefix
    if storage.exists(static_key(prefix, "feed_info", version)):
        return StaticResult(feed_version=version, skipped=True)
    result = StaticResult(feed_version=version, skipped=False)
    for table, model in _TABLES:
        if not gtfs_source.has_table(table):
            rows: list[dict[str, Any]] = []
        else:
            rows = [
                {**r.model_dump(), "feed_version": version} for r in gtfs_source.iter_rows(model)
            ]
        buf = io.BytesIO()
        pq.write_table(to_table(table, rows), buf, compression="zstd")
        storage.put_bytes(static_key(prefix, table, version), buf.getvalue())
        result.rows[table] = len(rows)
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
