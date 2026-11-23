"""Replay a raw/manifests tree into a curated target (local filesystem)."""

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow.parquet as pq

from headway.config import CityConfig
from headway.curate.compact import compact_all
from headway.curate.pipeline import curate_new
from headway.curate.schemas import META_SCHEMAS
from headway.curate.static import StaticResult, load_static
from headway.parse.gtfs_static import GtfsSource
from headway.storage import LocalStorage, Storage

_CURATED_TABLE = re.compile(r"^curated/[^/]+/(?:static/)?table=([^/]+)/")
_QUARANTINE_TABLE = re.compile(r"^quarantine/[^/]+/table=([^/]+)/")


@dataclass
class Summary:
    """Row counts in the curated layer and quarantine counts by table and reason."""

    curated: dict[str, int] = field(default_factory=dict)
    static: dict[str, int] = field(default_factory=dict)
    quarantine: dict[str, dict[str, int]] = field(default_factory=dict)
    # Counted by the curate run itself (filtered rows leave no trace in storage).
    filtered_out_of_scope: dict[str, int] = field(default_factory=dict)


def summarize(storage: Storage, config: CityConfig) -> Summary:
    out = Summary()
    prefix = config.storage_prefix
    for key in storage.list_keys(f"curated/{prefix}/"):
        m = _CURATED_TABLE.match(key)
        if m is None or not key.endswith(".parquet"):
            continue
        n = pq.read_metadata(io.BytesIO(storage.get_bytes(key))).num_rows
        if m.group(1) in META_SCHEMAS:  # bookkeeping copies of manifests/quarantine, not data
            continue
        bucket = out.static if "/static/" in key else out.curated
        bucket[m.group(1)] = bucket.get(m.group(1), 0) + n
    for key in storage.list_keys(f"quarantine/{prefix}/"):
        m = _QUARANTINE_TABLE.match(key)
        if m is None:
            continue
        by_reason = out.quarantine.setdefault(m.group(1), {})
        for line in storage.get_bytes(key).splitlines():
            reason = json.loads(line)["reason"]
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return out


def stage_inputs(source: Storage, target: Storage, config: CityConfig) -> int:
    """Copy raw captures and manifests of the city into the target; returns object count."""
    n = 0
    for root in ("raw", "manifests"):
        for key in source.list_keys(f"{root}/{config.storage_prefix}/"):
            target.put_bytes(key, source.get_bytes(key))
            n += 1
    return n


def replay(
    source_dir: Path, target_dir: Path, config: CityConfig, compact: bool = False
) -> tuple[Summary, StaticResult | None]:
    """load_static (if gtfs/ exists) + curate_new + optional compaction of every hour."""
    source = LocalStorage(source_dir)
    target = LocalStorage(target_dir)
    stage_inputs(source, target, config)
    static: StaticResult | None = None
    gtfs = source_dir / "gtfs"
    if gtfs.is_dir():
        static = load_static(target, config, GtfsSource(gtfs))
    curated = curate_new(target, config)
    if compact:
        compact_all(target, config)
    summary = summarize(target, config)
    summary.filtered_out_of_scope = curated.filtered_out_of_scope()
    return summary, static
