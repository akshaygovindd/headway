"""One capture run: fetch the realtime feeds, store raw bytes, write a manifest."""

import gzip
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ulid import ULID

from headway.config import CityConfig
from headway.contracts.manifest import ManifestEntry
from headway.contracts.types import FeedName
from headway.ingest.fetch import FetchResult
from headway.parse.gtfs_rt import ParseError, parse_header
from headway.storage import Storage

Fetcher = Callable[[str], FetchResult]
Clock = Callable[[], datetime]

FEEDS: tuple[FeedName, ...] = ("trip_updates", "vehicle_positions", "alerts")


@dataclass(frozen=True)
class FeedCapture:
    """Outcome of fetching one feed: its manifest entry and the raw body (None on failure)."""

    entry: ManifestEntry
    body: bytes | None


@dataclass(frozen=True)
class CaptureRun:
    """Outcome of one run: per-feed captures, and whether another delivery got there first."""

    captures: list[FeedCapture]
    manifest_key: str
    duplicate_key: str | None = None  # set when this run's manifest lost the claim on its minute

    @property
    def duplicate(self) -> bool:
        return self.duplicate_key is not None


def _partition(ts: datetime) -> str:
    ts = ts.astimezone(UTC)
    return f"dt={ts:%Y-%m-%d}/hour={ts:%H}"


def _capture_entry(
    config: CityConfig,
    storage: Storage,
    feed: FeedName,
    result: FetchResult,
    run_id: str,
    scheduled_at: datetime,
) -> ManifestEntry:
    fetched_at = result.fetched_at.astimezone(UTC)
    stamp = f"{fetched_at:%Y%m%dT%H%M%SZ}"
    common = {
        "run_id": run_id,
        "feed": feed,
        "city_id": config.city_id,
        "http_status": result.http_status,
        "scheduled_at": scheduled_at,
        "fetched_at": fetched_at,
    }
    if result.status != "ok" or result.body is None:
        return ManifestEntry(
            **common,
            capture_id=f"{feed}-{stamp}-error",
            status="error",
            error=result.error or "fetch failed",
        )

    body = result.body
    content_hash = hashlib.sha256(body).hexdigest()
    capture_id = f"{feed}-{stamp}-{content_hash[:8]}"
    key = f"raw/{config.storage_prefix}/feed={feed}/{_partition(fetched_at)}/{capture_id}.pb.gz"
    storage.put_bytes(key, gzip.compress(body, mtime=0))

    feed_header_ts: datetime | None = None
    entity_count: int | None = None
    header_error: str | None = None
    try:
        feed_header_ts, entity_count, _version = parse_header(body)
    except ParseError as exc:
        header_error = str(exc)

    return ManifestEntry(
        **common,
        capture_id=capture_id,
        status="ok",
        feed_header_ts=feed_header_ts,
        entity_count=entity_count,
        bytes=len(body),
        content_hash=content_hash,
        s3_key=key,
        header_parse_error=header_error,
    )


def manifest_key(config: CityConfig, scheduled_at: datetime) -> str:
    """Manifest key for a scheduled minute: one object per minute, whatever delivers it."""
    minute = scheduled_at.astimezone(UTC).replace(second=0, microsecond=0)
    return f"manifests/{config.storage_prefix}/{_partition(minute)}/{minute:%Y%m%dT%H%MZ}.json"


def duplicate_key(config: CityConfig, scheduled_at: datetime, run_id: str) -> str:
    minute = scheduled_at.astimezone(UTC).replace(second=0, microsecond=0)
    return (
        f"manifests/{config.storage_prefix}/duplicates/{_partition(minute)}/"
        f"{minute:%Y%m%dT%H%MZ}-{run_id}.json"
    )


def capture_run(
    config: CityConfig,
    storage: Storage,
    fetcher: Fetcher,
    clock: Clock,
    scheduled_at: datetime,
) -> CaptureRun:
    """Fetch all realtime feeds once and claim the minute's manifest.

    The manifest is written with a conditional create. EventBridge Scheduler delivers at least
    once, so the same minute can arrive twice; the delivery that loses the create writes a
    duplicate-delivery record instead (same shape, under ``manifests/<city>/duplicates/``). The
    record is flagged downstream, never silently dropped, and curation does not treat its
    captures as data.
    """
    run_id = str(ULID.from_datetime(clock()))
    urls: dict[FeedName, str] = {
        "trip_updates": str(config.feeds.trip_updates),
        "vehicle_positions": str(config.feeds.vehicle_positions),
        "alerts": str(config.feeds.alerts),
    }
    captures: list[FeedCapture] = []
    for feed in FEEDS:
        result = fetcher(urls[feed])
        entry = _capture_entry(config, storage, feed, result, run_id, scheduled_at)
        captures.append(FeedCapture(entry, result.body if entry.status == "ok" else None))
    entries = [c.entry for c in captures]
    manifest = {
        "run_id": run_id,
        "entries": [e.model_dump(mode="json") for e in entries],
    }
    key = manifest_key(config, scheduled_at)
    body = json.dumps(manifest, indent=2, sort_keys=True).encode()
    if storage.put_if_absent(key, body):
        return CaptureRun(captures, key)
    dup_key = duplicate_key(config, scheduled_at, run_id)
    record = {**manifest, "duplicate_of": key}
    storage.put_bytes(dup_key, json.dumps(record, indent=2, sort_keys=True).encode())
    return CaptureRun(captures, key, dup_key)
