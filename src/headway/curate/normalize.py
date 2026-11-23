"""Normalize raw captures into curated parquet parts and quarantine files.

Curation never dedupes captures: every ``ok`` capture is kept, so duplicate-content captures with
distinct capture_ids all land in the curated tables (``content_hash`` is carried for downstream
detection). The same capture_id given twice in one batch is processed once.
"""

import gzip
import hashlib
import io
import json
from collections import defaultdict
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pyarrow.parquet as pq
from pydantic import BaseModel

from headway.config import CityConfig
from headway.contracts.manifest import ManifestEntry
from headway.contracts.quarantine import QuarantinedRow, QuarantineReason, validate_records
from headway.contracts.records import AlertRecord, TripUpdateRecord, VehiclePositionRecord
from headway.curate.schemas import to_table
from headway.parse.gtfs_rt import (
    CaptureMeta,
    ParseError,
    Row,
    parse_alerts,
    parse_trip_updates,
    parse_vehicle_positions,
)
from headway.storage import Storage

Parser = Callable[[bytes, CaptureMeta], list[Row]]

FEEDS: dict[str, tuple[Parser, type[BaseModel]]] = {
    "trip_updates": (parse_trip_updates, TripUpdateRecord),
    "vehicle_positions": (parse_vehicle_positions, VehiclePositionRecord),
    "alerts": (parse_alerts, AlertRecord),
}


@dataclass
class BatchResult:
    batch_id: str
    captures: int = 0
    rows: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    quarantined: dict[str, dict[str, int]] = field(default_factory=dict)
    filtered_out_of_scope: dict[str, int] = field(default_factory=dict)
    keys: list[str] = field(default_factory=list)

    def count_quarantine(self, table: str, reason: str, n: int = 1) -> None:
        by_reason = self.quarantined.setdefault(table, {})
        by_reason[reason] = by_reason.get(reason, 0) + n


def batch_id_for(capture_ids: Collection[str]) -> str:
    """Stable id for a set of captures, independent of order and duplicates."""
    digest = hashlib.sha256("\n".join(sorted(set(capture_ids))).encode()).hexdigest()
    return digest[:16]


def curated_key(prefix: str, table: str, ts: datetime, name: str) -> str:
    ts = ts.astimezone(UTC)
    return f"curated/{prefix}/table={table}/dt={ts:%Y-%m-%d}/hour={ts:%H}/{name}"


def quarantine_key(prefix: str, table: str, ts: datetime, capture_id: str) -> str:
    ts = ts.astimezone(UTC)
    return f"quarantine/{prefix}/table={table}/dt={ts:%Y-%m-%d}/{capture_id}.jsonl"


def _json_default(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def _quarantine_lines(entry: ManifestEntry, rows: list[QuarantinedRow]) -> bytes:
    lines = [
        json.dumps(
            {
                "capture_id": entry.capture_id,
                "reason": q.reason.value,
                "error": q.error,
                "row": q.row,
            },
            default=_json_default,
            sort_keys=True,
        )
        for q in rows
    ]
    return ("\n".join(lines) + "\n").encode()


def quarantine_row(
    entry: ManifestEntry, table: str, line_no: int, q: QuarantinedRow
) -> dict[str, Any]:
    """The curated ``quarantine`` row for line ``line_no`` of the capture's JSONL object."""
    return {
        "capture_id": entry.capture_id,
        "source_table": table,
        "reason": q.reason.value,
        "error": q.error,
        "line_no": line_no,
        "fetched_at": entry.fetched_at,
    }


def _curated_row(entry: ManifestEntry, model: BaseModel) -> dict[str, Any]:
    row = model.model_dump()
    row.update(run_id=entry.run_id, city_id=entry.city_id, content_hash=entry.content_hash)
    start = row.get("start_date")
    if isinstance(start, str):
        row["start_date"] = datetime.strptime(start, "%Y%m%d").date()
    return row


def _parquet_bytes(table_name: str, rows: list[dict[str, Any]]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(to_table(table_name, rows), buf, compression="zstd")
    return buf.getvalue()


def normalize_batch(
    storage: Storage,
    config: CityConfig,
    manifest_entries: list[ManifestEntry],
    dim_routes: Mapping[str, int],
) -> BatchResult:
    """Parse, validate and write one batch of captures; returns counts and written keys."""
    seen: set[str] = set()
    entries: list[ManifestEntry] = []
    for e in sorted(manifest_entries, key=lambda e: (e.fetched_at, e.capture_id)):
        if e.status == "ok" and e.capture_id not in seen:
            seen.add(e.capture_id)
            entries.append(e)

    result = BatchResult(batch_id=batch_id_for(seen), captures=len(entries))
    prefix = config.storage_prefix
    in_scope = set(config.route_types)
    # (table, dt, hour) -> rows
    parts: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    part_ts: dict[tuple[str, str, str], datetime] = {}
    quarantine_writes: list[tuple[str, bytes]] = []

    for entry in entries:
        table = entry.feed
        parser, model = FEEDS[table]
        assert entry.s3_key is not None
        meta = CaptureMeta(capture_id=entry.capture_id, fetched_at=entry.fetched_at)
        bad: list[QuarantinedRow] = []
        valid: list[BaseModel] = []
        try:
            data = gzip.decompress(storage.get_bytes(entry.s3_key))
            raw_rows = parser(data, meta)
        except (ParseError, OSError, EOFError) as exc:
            bad.append(
                QuarantinedRow(
                    row={"capture_id": entry.capture_id, "s3_key": entry.s3_key},
                    reason=QuarantineReason.PROTOBUF_PARSE_ERROR,
                    error=str(exc),
                )
            )
        else:
            ctx = {"bbox": config.bbox} if table == "vehicle_positions" else None
            valid, bad = validate_records(raw_rows, model, ctx)

        kept: list[BaseModel] = []
        for rec in valid:
            route_id = getattr(rec, "route_id", None)
            if route_id is None:
                kept.append(rec)  # stop-only or agency-wide: no route to scope by
            elif route_id not in dim_routes:
                bad.append(
                    QuarantinedRow(
                        row=rec.model_dump(mode="json"),
                        reason=QuarantineReason.UNKNOWN_ROUTE,
                        error=f"route_id {route_id!r} not in current GTFS routes",
                    )
                )
            elif dim_routes[route_id] in in_scope:
                kept.append(rec)
            else:
                result.filtered_out_of_scope[table] = result.filtered_out_of_scope.get(table, 0) + 1

        if kept:
            ts = entry.fetched_at.astimezone(UTC)
            pkey = (table, f"{ts:%Y-%m-%d}", f"{ts:%H}")
            part_ts[pkey] = ts
            parts[pkey].extend(_curated_row(entry, rec) for rec in kept)
            result.rows[table] += len(kept)
        if bad:
            quarantine_writes.append(
                (
                    quarantine_key(prefix, table, entry.fetched_at, entry.capture_id),
                    _quarantine_lines(entry, bad),
                )
            )
            qts = entry.fetched_at.astimezone(UTC)
            qkey = ("quarantine", f"{qts:%Y-%m-%d}", f"{qts:%H}")
            part_ts[qkey] = qts
            parts[qkey].extend(quarantine_row(entry, table, i, q) for i, q in enumerate(bad))
            for q in bad:
                result.count_quarantine(table, q.reason.value)

    for part_key in sorted(parts):
        part_table = part_key[0]
        key = curated_key(prefix, part_table, part_ts[part_key], f"part-{result.batch_id}.parquet")
        storage.put_bytes(key, _parquet_bytes(part_table, parts[part_key]))
        result.keys.append(key)
    for key, body in quarantine_writes:
        storage.put_bytes(key, body, content_type="application/x-ndjson")
        result.keys.append(key)
    result.rows = dict(result.rows)
    return result
