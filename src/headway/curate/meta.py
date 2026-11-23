"""Curated Parquet copies of ingest bookkeeping: run manifests and quarantine.

The raw JSON objects (``manifests/`` and ``quarantine/``) stay the source of truth. Reading them
from the transform costs one S3 request per object, which grows with every ingest run, so
curation also writes one row per manifest entry and per quarantined record as hourly Parquet
parts that compaction merges like any other curated table. ``backfill_manifests`` rebuilds both
tables from the raw objects for data written before they existed.
"""

import io
import json
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyarrow.parquet as pq

from headway.config import CityConfig
from headway.contracts.manifest import ManifestEntry
from headway.curate.compact import COMPACTED
from headway.curate.normalize import batch_id_for, curated_key
from headway.curate.schemas import META_SCHEMAS, to_table
from headway.storage import Storage

READ_WORKERS = 16
_QUARANTINE_KEY = re.compile(r"^quarantine/[^/]+/table=([^/]+)/dt=([^/]+)/([^/]+)\.jsonl$")

# Status of manifest rows that come from a duplicate-delivery record (a second scheduler
# delivery of an already-claimed minute). dbt flags and counts them; they are never data.
DUPLICATE_STATUS = "duplicate_delivery"
DUPLICATES_SEGMENT = "/duplicates/"

Row = dict[str, Any]
HourKey = tuple[str, str]  # (dt, hour), UTC


def _hour_of(ts: datetime) -> HourKey:
    ts = ts.astimezone(UTC)
    return f"{ts:%Y-%m-%d}", f"{ts:%H}"


def is_duplicate_record(key: str) -> bool:
    return DUPLICATES_SEGMENT in key


def manifest_row(entry: ManifestEntry) -> Row:
    return {name: getattr(entry, name) for name in META_SCHEMAS["manifests"].names}


def _row_key(table: str, row: Row) -> tuple[Any, ...]:
    if table == "manifests":
        return (row["run_id"], row["capture_id"])
    return (row["capture_id"], row["source_table"], row["line_no"])


def _sort_key(table: str, row: Row) -> tuple[Any, ...]:
    return (row["fetched_at"] or datetime.min.replace(tzinfo=UTC), *_row_key(table, row))


def _parquet(table: str, rows: list[Row]) -> bytes:
    buf = io.BytesIO()
    pq.write_table(to_table(table, rows), buf, compression="zstd")
    return buf.getvalue()


def write_manifest_parts(
    storage: Storage, config: CityConfig, entries: Iterable[ManifestEntry]
) -> list[str]:
    """Write one ``table=manifests`` part per UTC hour of ``fetched_at``; returns the keys.

    The part name is derived from the capture ids, so rewriting the same entries is a no-op.
    """
    by_hour: dict[HourKey, dict[tuple[str, str], ManifestEntry]] = defaultdict(dict)
    for e in entries:
        by_hour[_hour_of(e.fetched_at)][(e.run_id, e.capture_id)] = e
    keys = []
    for hour in sorted(by_hour):
        group = sorted(by_hour[hour].values(), key=lambda e: (e.fetched_at, e.capture_id))
        name = f"part-{batch_id_for([e.capture_id for e in group])}.parquet"
        key = curated_key(config.storage_prefix, "manifests", group[0].fetched_at, name)
        storage.put_bytes(key, _parquet("manifests", [manifest_row(e) for e in group]))
        keys.append(key)
    return keys


def _read_all(storage: Storage, keys: list[str]) -> list[bytes]:
    with ThreadPoolExecutor(READ_WORKERS) as pool:
        return list(pool.map(storage.get_bytes, keys))


def read_raw_manifest_entries(storage: Storage, prefix: str) -> list[ManifestEntry]:
    """Every entry of every raw manifest object, read concurrently."""
    keys = [k for k in storage.list_keys(f"manifests/{prefix}/") if k.endswith(".json")]
    entries: list[ManifestEntry] = []
    for key, body in zip(keys, _read_all(storage, keys), strict=True):
        for e in json.loads(body)["entries"]:
            entry = ManifestEntry.model_validate(e)
            if is_duplicate_record(key):
                entry = entry.model_copy(update={"status": DUPLICATE_STATUS})
            entries.append(entry)
    return entries


def read_raw_quarantine(
    storage: Storage, prefix: str, fetched_at: Callable[[str], datetime | None]
) -> dict[HourKey, list[Row]]:
    """Every line of every raw quarantine object, grouped by UTC hour of its capture."""
    matches = [
        (k, m)
        for k in storage.list_keys(f"quarantine/{prefix}/")
        if (m := _QUARANTINE_KEY.match(k))
    ]
    bodies = _read_all(storage, [k for k, _ in matches])
    by_hour: dict[HourKey, list[Row]] = defaultdict(list)
    for (_, m), body in zip(matches, bodies, strict=True):
        table, dt, capture_id = m.group(1), m.group(2), m.group(3)
        ts = fetched_at(capture_id)
        # An orphan (capture missing from the manifests) is filed under the key's date, hour 00.
        hour = _hour_of(ts) if ts else (dt, "00")
        for line_no, line in enumerate(body.splitlines()):
            doc = json.loads(line)
            by_hour[hour].append(
                {
                    "capture_id": doc["capture_id"],
                    "source_table": table,
                    "reason": doc["reason"],
                    "error": doc["error"],
                    "line_no": line_no,
                    "fetched_at": ts,
                }
            )
    return by_hour


@dataclass(frozen=True)
class BackfillResult:
    manifest_rows: int
    quarantine_rows: int
    hours_rewritten: int

    def as_dict(self) -> dict[str, int]:
        return {
            "manifest_rows": self.manifest_rows,
            "quarantine_rows": self.quarantine_rows,
            "hours_rewritten": self.hours_rewritten,
        }


def _merge_hour(
    storage: Storage, prefix: str, table: str, hour: HourKey, rows: list[Row]
) -> tuple[int, bool]:
    """Merge ``rows`` with whatever the hour partition already holds into one compacted part.

    Existing rows are kept (live curation may have written some), duplicates are dropped by row
    key, and only the part files that were read are removed. Returns (rows, rewritten).
    """
    dt, hh = hour
    base = f"curated/{prefix}/table={table}/dt={dt}/hour={hh}/"
    existing = [k for k in storage.list_keys(base) if k.startswith(base + "part-")]
    schema = META_SCHEMAS[table]
    merged: dict[tuple[Any, ...], Row] = {}
    for key in existing:
        for row in pq.read_table(io.BytesIO(storage.get_bytes(key)), schema=schema).to_pylist():
            merged[_row_key(table, row)] = row
    for row in rows:
        merged[_row_key(table, row)] = row  # raw is the source of truth
    ordered = sorted(merged.values(), key=lambda r: _sort_key(table, r))
    body = _parquet(table, ordered)
    target = base + COMPACTED
    if existing == [target] and storage.get_bytes(target) == body:
        return len(ordered), False
    storage.put_bytes(target, body)
    for key in existing:
        if key != target:
            storage.delete(key)
    return len(ordered), True


def backfill_manifests(storage: Storage, config: CityConfig) -> BackfillResult:
    """Build ``table=manifests`` and ``table=quarantine`` for all existing raw data.

    Idempotent: each hour partition is rebuilt from the raw objects merged with what is already
    curated, so re-running, or running next to live curation, never duplicates rows.
    """
    prefix = config.storage_prefix
    entries = read_raw_manifest_entries(storage, prefix)
    captured: dict[str, datetime] = {e.capture_id: e.fetched_at for e in entries}

    manifests: dict[HourKey, list[Row]] = defaultdict(list)
    for e in entries:
        manifests[_hour_of(e.fetched_at)].append(manifest_row(e))
    quarantine = read_raw_quarantine(storage, prefix, captured.get)

    totals = {"manifests": 0, "quarantine": 0}
    rewritten = 0
    for table, groups in (("manifests", manifests), ("quarantine", quarantine)):
        for hour in sorted(groups):
            n, changed = _merge_hour(storage, prefix, table, hour, groups[hour])
            totals[table] += n
            rewritten += changed
    return BackfillResult(totals["manifests"], totals["quarantine"], rewritten)
