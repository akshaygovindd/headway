"""AWS Lambda entry point: one capture run per invocation."""

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from headway import chaos
from headway.config import CityConfig, load_city_config
from headway.ingest import emf
from headway.ingest.capture import POLL_OFFSETS_S, Clock, FeedCapture, Fetcher, Sleeper, capture_run
from headway.ingest.fetch import FetchResult, fetch_feed
from headway.ingest.live import (
    LIVE_CACHE_CONTROL,
    LIVE_CONTENT_TYPE,
    build_live_snapshot,
    live_snapshot_key,
)
from headway.parse.gtfs_rt import CaptureMeta, ParseError
from headway.storage import S3Storage, Storage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Created once per execution environment so warm invocations reuse connections.
_client = httpx.Client(follow_redirects=True)


# Polls 1-3 of a minute must not eat the run: one attempt, short timeout, no backoff.
POLL_TIMEOUT_S = 5.0


def _fetch(url: str) -> FetchResult:
    return fetch_feed(url, _client)


def _poll_fetch(url: str) -> FetchResult:
    return fetch_feed(url, _client, backoffs=(), timeout=POLL_TIMEOUT_S)


def _chaos_fetch(url: str) -> FetchResult:
    """The feed_down drill (ADR-017): nothing is fetched and every feed is recorded as an error."""
    return FetchResult("error", None, None, _now(), "chaos: feed_down")


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _now() -> datetime:
    return datetime.now(UTC)


def _scheduled_at(event: dict[str, Any], now: datetime) -> datetime:
    """Parse the scheduler timestamp; fall back to now floored to the minute."""
    fallback = now.replace(second=0, microsecond=0)
    raw = event.get("scheduled_at")
    try:
        parsed = datetime.fromisoformat(str(raw))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, TypeError):
        logger.warning("invalid or missing scheduled_at %r; using current minute", raw)
        return fallback


def run_minute(
    config: CityConfig,
    raw_storage: Storage,
    public_storage: Storage,
    scheduled_at: datetime,
    *,
    fetcher: Fetcher,
    poll_fetcher: Fetcher,
    clock: Clock,
    sleep: Sleeper,
    polls: int = 1,
) -> dict[str, Any]:
    """One scheduled minute: ``polls`` timed polls, live snapshot after each vehicle poll."""
    started = time.monotonic()
    vehicle_count: int | None = None
    snapshot_errors: list[Exception] = []

    def update_snapshot(capture: FeedCapture) -> None:
        nonlocal vehicle_count
        if capture.body is None:
            return
        meta = CaptureMeta(capture.entry.capture_id, capture.entry.fetched_at)
        try:
            snapshot = build_live_snapshot(capture.body, meta, config)
            public_storage.put_bytes(
                live_snapshot_key(config),
                json.dumps(snapshot, separators=(",", ":")).encode(),
                content_type=LIVE_CONTENT_TYPE,
                cache_control=LIVE_CACHE_CONTROL,
            )
        except ParseError as exc:
            logger.warning("live snapshot skipped: %s", exc)
        except Exception as exc:
            # A failed snapshot must not cost the minute's captures or its manifest: remember
            # the first failure and raise it once the manifest is written, so it still alarms.
            logger.exception("live snapshot write failed")
            snapshot_errors.append(exc)
        else:
            vehicle_count = snapshot["vehicle_count"]

    run = capture_run(
        config,
        raw_storage,
        fetcher,
        clock,
        scheduled_at,
        sleep=sleep,
        poll_fetcher=poll_fetcher,
        offsets=POLL_OFFSETS_S[:polls],
        on_vehicle_capture=update_snapshot,
    )
    if snapshot_errors:
        raise snapshot_errors[0]
    captures = run.captures
    run_id = captures[0].entry.run_id
    if run.duplicate:
        # Second delivery of the same scheduled minute: the first delivery owns the manifest.
        # Record it (done by capture_run), log it, and finish cleanly. Its polls may have
        # refreshed the live snapshot with equally fresh data, which is harmless.
        summary: dict[str, Any] = {
            "run_id": run_id,
            "scheduled_at": scheduled_at.isoformat(),
            "duplicate_delivery": True,
            "duplicate_record": run.duplicate_key,
            "duplicate_of": run.manifest_key,
            "duration_ms": round((time.monotonic() - started) * 1000),
        }
        logger.warning("duplicate delivery: %s", json.dumps(summary, sort_keys=True))
        return summary

    first = {c.entry.feed: c.entry.status for c in captures if c.entry.poll_index == 0}
    vehicle_polls = [c.entry.status for c in captures if c.entry.feed == "vehicle_positions"]
    emf.emit(
        emf.run_metrics(
            clock(),
            {feed: int(status == "error") for feed, status in first.items()},
            sum(1 for status in vehicle_polls if status == "ok"),
        )
    )
    summary = {
        "run_id": run_id,
        "scheduled_at": scheduled_at.isoformat(),
        "statuses": first,
        "vehicle_polls": vehicle_polls,
        "skipped_polls": list(run.skipped_polls),
        "bytes": {c.entry.feed: c.entry.bytes for c in captures if c.entry.poll_index == 0},
        "vehicle_count": vehicle_count,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    logger.info(json.dumps(summary, sort_keys=True))
    return summary


def _polls() -> int:
    """VEHICLE_POLLS: 1 is the single poll per minute, 4 is 15 s sampling; anything else is 1."""
    raw = os.environ.get("VEHICLE_POLLS", "1")
    try:
        return max(1, min(len(POLL_OFFSETS_S), int(raw)))
    except ValueError:
        logger.warning("invalid VEHICLE_POLLS %r; using 1", raw)
        return 1


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    config = load_city_config(os.environ["CITY_ID"])
    raw_storage = S3Storage(os.environ["RAW_BUCKET"])
    public_storage = S3Storage(os.environ["PUBLIC_BUCKET"])
    scheduled_at = _scheduled_at(event or {}, _now())
    drill = chaos.read(_now()).active("feed_down")
    return run_minute(
        config,
        raw_storage,
        public_storage,
        scheduled_at,
        fetcher=_chaos_fetch if drill else _fetch,
        poll_fetcher=_chaos_fetch if drill else _poll_fetch,
        clock=_now,
        sleep=_sleep,
        polls=_polls(),
    )
