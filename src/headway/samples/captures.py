"""In-memory view of captured runs: load from storage, rebuild entries, write back."""

import gzip
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC
from pathlib import Path
from typing import Any

from headway.contracts.manifest import ManifestEntry
from headway.ingest.capture import _partition
from headway.parse.gtfs_rt import ParseError, parse_header
from headway.storage import LocalStorage


@dataclass(frozen=True)
class Capture:
    entry: ManifestEntry
    body: bytes | None  # uncompressed feed bytes; None for failed fetches


@dataclass(frozen=True)
class Run:
    run_id: str
    captures: tuple[Capture, ...]

    @property
    def scheduled_at(self) -> Any:
        return self.captures[0].entry.scheduled_at

    def capture(self, feed: str) -> Capture | None:
        return next((c for c in self.captures if c.entry.feed == feed), None)

    def replace_capture(self, feed: str, new: Capture) -> "Run":
        return replace(
            self, captures=tuple(new if c.entry.feed == feed else c for c in self.captures)
        )


def rebuild(entry: ManifestEntry, body: bytes, prefix: str) -> Capture:
    """Recompute hash, ids, key and header fields of an entry for new body bytes."""
    digest = hashlib.sha256(body).hexdigest()
    fetched = entry.fetched_at.astimezone(UTC)
    capture_id = f"{entry.feed}-{fetched:%Y%m%dT%H%M%SZ}-{digest[:8]}"
    key = f"raw/{prefix}/feed={entry.feed}/{_partition(fetched)}/{capture_id}.pb.gz"
    header_ts = None
    count = None
    error = None
    try:
        header_ts, count, _version = parse_header(body)
    except ParseError as exc:
        error = str(exc)
    new = entry.model_copy(
        update={
            "capture_id": capture_id,
            "content_hash": digest,
            "s3_key": key,
            "bytes": len(body),
            "feed_header_ts": header_ts,
            "entity_count": count,
            "header_parse_error": error,
        }
    )
    return Capture(new, body)


def load_runs(root: Path, prefix: str) -> list[Run]:
    storage = LocalStorage(root)
    runs: list[Run] = []
    for key in sorted(storage.list_keys(f"manifests/{prefix}/")):
        doc = json.loads(storage.get_bytes(key))
        captures = []
        for raw in doc["entries"]:
            entry = ManifestEntry.model_validate(raw)
            body = None
            if entry.status == "ok" and entry.s3_key:
                body = gzip.decompress(storage.get_bytes(entry.s3_key))
            captures.append(Capture(entry, body))
        runs.append(Run(doc["run_id"], tuple(captures)))
    runs.sort(key=lambda r: (r.scheduled_at, r.run_id))
    return runs


def write_runs(root: Path, prefix: str, runs: list[Run]) -> None:
    storage = LocalStorage(root)
    for run in runs:
        for cap in run.captures:
            if cap.body is not None and cap.entry.s3_key:
                storage.put_bytes(cap.entry.s3_key, gzip.compress(cap.body, mtime=0))
        doc = {
            "run_id": run.run_id,
            "entries": [c.entry.model_dump(mode="json") for c in run.captures],
        }
        key = f"manifests/{prefix}/{_partition(run.scheduled_at)}/{run.run_id}.json"
        storage.put_bytes(key, json.dumps(doc, indent=2, sort_keys=True).encode())
