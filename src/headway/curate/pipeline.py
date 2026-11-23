"""Incremental curation driven by a watermark over manifest entries."""

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from headway.config import CityConfig
from headway.contracts.manifest import ManifestEntry
from headway.curate.meta import (
    DUPLICATE_STATUS,
    READ_WORKERS,
    is_duplicate_record,
    write_manifest_parts,
)
from headway.curate.normalize import BatchResult, normalize_batch
from headway.curate.static import load_dim_routes
from headway.storage import Storage

# Manifests are partitioned by scheduled_at; fetch happens shortly after, so a small margin is
# enough to skip partitions that cannot hold anything newer than the watermark.
_PARTITION_MARGIN = timedelta(hours=2)
_PARTITION = re.compile(r"/dt=(\d{4}-\d{2}-\d{2})/hour=(\d{2})/")


@dataclass(frozen=True)
class Watermark:
    """Last processed fetched_at plus the capture_ids seen at exactly that instant."""

    fetched_at: datetime
    capture_ids: frozenset[str]

    def covers(self, entry: ManifestEntry) -> bool:
        if entry.fetched_at != self.fetched_at:
            return entry.fetched_at < self.fetched_at
        return entry.capture_id in self.capture_ids


@dataclass
class CurateResult:
    batches: list[BatchResult] = field(default_factory=list)

    def rows(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for b in self.batches:
            for table, n in b.rows.items():
                totals[table] += n
        return dict(totals)

    def filtered_out_of_scope(self) -> dict[str, int]:
        totals: dict[str, int] = defaultdict(int)
        for b in self.batches:
            for table, n in b.filtered_out_of_scope.items():
                totals[table] += n
        return dict(totals)

    def quarantined(self) -> dict[str, dict[str, int]]:
        totals: dict[str, dict[str, int]] = {}
        for b in self.batches:
            for table, by_reason in b.quarantined.items():
                dest = totals.setdefault(table, {})
                for reason, n in by_reason.items():
                    dest[reason] = dest.get(reason, 0) + n
        return totals


def watermark_key(prefix: str) -> str:
    return f"state/{prefix}/curate_watermark.json"


def read_watermark(storage: Storage, prefix: str) -> Watermark | None:
    key = watermark_key(prefix)
    if not storage.exists(key):
        return None
    data = json.loads(storage.get_bytes(key))
    return Watermark(
        fetched_at=datetime.fromisoformat(data["fetched_at"]),
        capture_ids=frozenset(data["capture_ids"]),
    )


def write_watermark(storage: Storage, prefix: str, mark: Watermark) -> None:
    body = {
        "fetched_at": mark.fetched_at.astimezone(UTC).isoformat(),
        "capture_ids": sorted(mark.capture_ids),
    }
    storage.put_bytes(
        watermark_key(prefix),
        (json.dumps(body, sort_keys=True) + "\n").encode(),
        content_type="application/json",
    )


def _advance(mark: Watermark | None, entries: list[ManifestEntry]) -> Watermark:
    latest = max(e.fetched_at for e in entries)
    ids = {e.capture_id for e in entries if e.fetched_at == latest}
    if mark is not None and mark.fetched_at == latest:
        ids |= mark.capture_ids
    return Watermark(fetched_at=latest, capture_ids=frozenset(ids))


def list_manifest_entries(
    storage: Storage, prefix: str, since: datetime | None = None
) -> list[ManifestEntry]:
    return _read_entries(storage, _manifest_keys(storage, prefix, since, duplicates=False))


def list_duplicate_entries(
    storage: Storage, prefix: str, since: datetime | None = None
) -> list[ManifestEntry]:
    """Entries of duplicate-delivery records. They are flagged in the manifests table but their
    captures are never curated as data: the delivery that claimed the minute owns it."""
    keys = _manifest_keys(storage, prefix, since, duplicates=True)
    return [e.model_copy(update={"status": DUPLICATE_STATUS}) for e in _read_entries(storage, keys)]


def _manifest_keys(
    storage: Storage, prefix: str, since: datetime | None, *, duplicates: bool
) -> list[str]:
    keys = []
    for key in storage.list_keys(f"manifests/{prefix}/"):
        if is_duplicate_record(key) != duplicates:
            continue
        m = _PARTITION.search(key)
        if since is not None and m is not None:
            start = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}:00:00+00:00")
            if start + _PARTITION_MARGIN <= since:
                continue
        keys.append(key)
    return keys


def _read_entries(storage: Storage, keys: list[str]) -> list[ManifestEntry]:
    with ThreadPoolExecutor(READ_WORKERS) as pool:
        docs = list(pool.map(lambda k: json.loads(storage.get_bytes(k)), keys))
    return [ManifestEntry.model_validate(e) for doc in docs for e in doc["entries"]]


def curate_new(
    storage: Storage, config: CityConfig, dim_routes: Mapping[str, int] | None = None
) -> CurateResult:
    """Curate manifest entries newer than the watermark, one hourly group at a time.

    The watermark moves forward only after every write of a group has succeeded, so a failed
    run is simply retried and rewrites the same deterministic files.
    """
    prefix = config.storage_prefix
    mark = read_watermark(storage, prefix)
    routes = load_dim_routes(storage, config) if dim_routes is None else dim_routes
    fresh = [
        e
        for e in list_manifest_entries(storage, prefix, mark.fetched_at if mark else None)
        if mark is None or not mark.covers(e)
    ]
    groups: dict[datetime, list[ManifestEntry]] = defaultdict(list)
    for e in fresh:
        groups[e.fetched_at.astimezone(UTC).replace(minute=0, second=0, microsecond=0)].append(e)

    result = CurateResult()
    for hour in sorted(groups):
        group = groups[hour]
        result.batches.append(normalize_batch(storage, config, group, routes))
        write_manifest_parts(storage, config, group)
        mark = _advance(mark, group)
        write_watermark(storage, prefix, mark)
    # Rewriting is idempotent (part names derive from the entries), so the records are re-listed
    # over the same partition window on every run rather than tracked by the watermark.
    duplicates = list_duplicate_entries(storage, prefix, mark.fetched_at if mark else None)
    if duplicates:
        write_manifest_parts(storage, config, duplicates)
    return result
