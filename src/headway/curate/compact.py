"""Merge the part files of one hourly partition into a single compacted file."""

import io
import re
from dataclasses import dataclass

import pyarrow as pa
import pyarrow.parquet as pq

from headway.config import CityConfig
from headway.curate.schemas import REALTIME_SCHEMAS
from headway.storage import Storage

COMPACTED = "part-compacted.parquet"
_HOUR_DIR = re.compile(r"^curated/[^/]+/table=([^/]+)/dt=([^/]+)/hour=([^/]+)/")


class CompactionError(Exception):
    """The merged file did not contain exactly the rows of its inputs."""


@dataclass(frozen=True)
class CompactResult:
    compacted: bool
    rows: int
    inputs_removed: int


def _hour_prefix(prefix: str, table: str, dt: str, hour: str) -> str:
    return f"curated/{prefix}/table={table}/dt={dt}/hour={hour}/"


def compact_hour(
    storage: Storage, config: CityConfig, table: str, dt: str, hour: str
) -> CompactResult:
    """Merge ``part-*.parquet`` into ``part-compacted.parquet`` for one table/dt/hour.

    Inputs are deleted only after the merged file's row count equals the sum of the inputs'.
    Re-running on an already compacted hour changes nothing.
    """
    base = _hour_prefix(config.storage_prefix, table, dt, hour)
    keys = storage.list_keys(base)
    compacted_key = base + COMPACTED
    parts = [k for k in keys if k.startswith(base + "part-") and k != compacted_key]
    if not parts:
        return CompactResult(compacted=False, rows=0, inputs_removed=0)

    inputs = ([compacted_key] if compacted_key in keys else []) + sorted(parts)
    schema = REALTIME_SCHEMAS[table]
    tables = [pq.read_table(io.BytesIO(storage.get_bytes(k)), schema=schema) for k in inputs]
    expected = sum(t.num_rows for t in tables)
    merged = pa.concat_tables(tables)

    buf = io.BytesIO()
    pq.write_table(merged, buf, compression="zstd")
    data = buf.getvalue()
    written = pq.read_metadata(io.BytesIO(data)).num_rows
    if written != expected:
        raise CompactionError(f"{base}: merged {written} rows, inputs hold {expected}")

    storage.put_bytes(compacted_key, data)
    for key in parts:
        storage.delete(key)
    return CompactResult(compacted=True, rows=written, inputs_removed=len(parts))


def compact_all(storage: Storage, config: CityConfig) -> list[CompactResult]:
    """Compact every realtime hour partition that still has loose part files."""
    seen: set[tuple[str, str, str]] = set()
    for key in storage.list_keys(f"curated/{config.storage_prefix}/table="):
        m = _HOUR_DIR.match(key)
        if m and m.group(1) in REALTIME_SCHEMAS:
            seen.add((m.group(1), m.group(2), m.group(3)))
    return [compact_hour(storage, config, t, d, h) for t, d, h in sorted(seen)]
