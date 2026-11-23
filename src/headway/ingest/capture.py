"""One capture run: fetch the realtime feeds, store raw bytes, write a manifest."""

import gzip
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ulid import ULID

from headway.config import CityConfig
from headway.contracts.manifest import ManifestEntry
from headway.contracts.types import FeedName
from headway.ingest.fetch import FetchResult
from headway.parse.gtfs_rt import ParseError, parse_header
from headway.storage import Storage

Fetcher = Callable[[str], FetchResult]
Clock = Callable[[], datetime]
Sleeper = Callable[[float], None]
VehicleHook = Callable[["FeedCapture"], None]

FEEDS: tuple[FeedName, ...] = ("trip_updates", "vehicle_positions", "alerts")

# Poll k of a minute is due k * 15 s after the scheduled time. Poll 0 fetches every feed; later
# polls fetch VehiclePositions only (the feed republishes about every 5 s).
POLL_OFFSETS_S: tuple[int, ...] = (0, 15, 30, 45)
LATE_SKIP_S = 5.0  # a poll more than this past its slot is skipped, not fetched late
RUN_DEADLINE_S = 55.0  # the run, manifest included, should end this long after the scheduled time


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
    skipped_polls: tuple[int, ...] = ()  # poll indexes dropped for being late or past the deadline

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
    poll_index: int = 0,
) -> ManifestEntry:
    fetched_at = result.fetched_at.astimezone(UTC)
    stamp = f"{fetched_at:%Y%m%dT%H%M%SZ}"
    common = {
        "run_id": run_id,
        "feed": feed,
        "city_id": config.city_id,
        "http_status": result.http_status,
        "scheduled_at": scheduled_at,
        "poll_index": poll_index,
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
    *,
    sleep: Sleeper = time.sleep,
    poll_fetcher: Fetcher | None = None,
    offsets: tuple[int, ...] = POLL_OFFSETS_S,
    on_vehicle_capture: VehicleHook | None = None,
) -> CaptureRun:
    """Poll the realtime feeds across one scheduled minute and claim the minute's manifest.

    Poll 0 fetches all feeds immediately. Each later poll sleeps until ``scheduled_at`` plus its
    offset and fetches VehiclePositions only, through ``poll_fetcher`` (default ``fetcher``). A
    poll more than ``LATE_SKIP_S`` past its slot, or one that would start after the run deadline,
    is skipped and reported in ``skipped_polls``. Every poll is its own capture with its own
    capture id and a ``poll_index``; all share the minute's ``scheduled_at``, so downstream
    coverage still counts one capture minute. ``on_vehicle_capture`` runs after each vehicle
    capture (the live snapshot).

    The minute is claimed first (see below) and the manifest completed at the end. EventBridge
    Scheduler delivers at least once, so the same minute can arrive twice; the delivery that
    loses the claim fetches nothing and writes a duplicate-delivery record instead (under
    ``manifests/<city>/duplicates/``). The record is flagged downstream, never silently dropped,
    and curation does not treat its captures as data.
    """
    run_id = str(ULID.from_datetime(clock()))
    key = manifest_key(config, scheduled_at)
    # Claim the minute before fetching anything: the manifest key is created empty with a
    # conditional create, and rewritten with the entries once the polls are done. A delivery that
    # loses the claim fetches nothing; it only records that it happened.
    claim = {"run_id": run_id, "entries": [], "claimed_at": clock().isoformat()}
    if not storage.put_if_absent(key, json.dumps(claim, indent=2, sort_keys=True).encode()):
        return _lost_claim(config, storage, clock, scheduled_at, run_id, key)
    urls: dict[FeedName, str] = {
        "trip_updates": str(config.feeds.trip_updates),
        "vehicle_positions": str(config.feeds.vehicle_positions),
        "alerts": str(config.feeds.alerts),
    }
    later = poll_fetcher or fetcher
    deadline = scheduled_at + timedelta(seconds=RUN_DEADLINE_S)
    captures: list[FeedCapture] = []
    skipped: list[int] = []

    def take(feed: FeedName, fetch: Fetcher, poll_index: int) -> None:
        result = fetch(urls[feed])
        entry = _capture_entry(config, storage, feed, result, run_id, scheduled_at, poll_index)
        capture = FeedCapture(entry, result.body if entry.status == "ok" else None)
        captures.append(capture)
        if feed == "vehicle_positions" and on_vehicle_capture is not None:
            on_vehicle_capture(capture)

    for poll_index, offset in enumerate(offsets):
        if poll_index == 0:
            for feed in FEEDS:
                take(feed, fetcher, 0)
            continue
        slot = scheduled_at + timedelta(seconds=offset)
        now = clock()
        if now > slot + timedelta(seconds=LATE_SKIP_S):
            skipped.append(poll_index)
            continue
        if now < slot:
            sleep((slot - now).total_seconds())
        if clock() >= deadline:
            skipped.append(poll_index)
            continue
        take("vehicle_positions", later, poll_index)

    entries = [c.entry for c in captures]
    manifest = {
        "run_id": run_id,
        "entries": [e.model_dump(mode="json") for e in entries],
    }
    storage.put_bytes(key, json.dumps(manifest, indent=2, sort_keys=True).encode())
    return CaptureRun(captures, key, skipped_polls=tuple(skipped))


def _lost_claim(
    config: CityConfig,
    storage: Storage,
    clock: Clock,
    scheduled_at: datetime,
    run_id: str,
    key: str,
) -> CaptureRun:
    """Record a second delivery of a claimed minute without fetching.

    The record holds one placeholder entry (an error capture, never fetched) so curation still
    flags the delivery and the dbt delivery count sees a second run for the minute.
    """
    now = clock().astimezone(UTC)
    entry = ManifestEntry(
        run_id=run_id,
        capture_id=f"duplicate_delivery-{now:%Y%m%dT%H%M%S}-{run_id[-8:]}",
        feed="vehicle_positions",
        city_id=config.city_id,
        status="error",
        scheduled_at=scheduled_at,
        fetched_at=now,
        error="duplicate delivery: minute already claimed, nothing fetched",
    )
    dup_key = duplicate_key(config, scheduled_at, run_id)
    record = {"run_id": run_id, "entries": [entry.model_dump(mode="json")], "duplicate_of": key}
    storage.put_bytes(dup_key, json.dumps(record, indent=2, sort_keys=True).encode())
    return CaptureRun([FeedCapture(entry, None)], key, dup_key)
