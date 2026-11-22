"""AWS Lambda entry point: one capture run per invocation."""

import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from headway.config import load_city_config
from headway.ingest.capture import capture_run
from headway.ingest.fetch import FetchResult, fetch_feed
from headway.ingest.live import (
    LIVE_CACHE_CONTROL,
    LIVE_CONTENT_TYPE,
    build_live_snapshot,
    live_snapshot_key,
)
from headway.parse.gtfs_rt import CaptureMeta, ParseError
from headway.storage import S3Storage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Created once per execution environment so warm invocations reuse connections.
_client = httpx.Client(follow_redirects=True)


def _fetch(url: str) -> FetchResult:
    return fetch_feed(url, _client)


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


def lambda_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    started = time.monotonic()
    config = load_city_config(os.environ["CITY_ID"])
    raw_storage = S3Storage(os.environ["RAW_BUCKET"])
    public_storage = S3Storage(os.environ["PUBLIC_BUCKET"])

    scheduled_at = _scheduled_at(event or {}, _now())
    captures = capture_run(config, raw_storage, _fetch, _now, scheduled_at)

    vehicle_count: int | None = None
    vehicles = next(c for c in captures if c.entry.feed == "vehicle_positions")
    if vehicles.body is not None:
        meta = CaptureMeta(vehicles.entry.capture_id, vehicles.entry.fetched_at)
        try:
            snapshot = build_live_snapshot(vehicles.body, meta, config)
        except ParseError as exc:
            logger.warning("live snapshot skipped: %s", exc)
        else:
            public_storage.put_bytes(
                live_snapshot_key(config),
                json.dumps(snapshot, separators=(",", ":")).encode(),
                content_type=LIVE_CONTENT_TYPE,
                cache_control=LIVE_CACHE_CONTROL,
            )
            vehicle_count = snapshot["vehicle_count"]

    summary = {
        "run_id": captures[0].entry.run_id,
        "scheduled_at": scheduled_at.isoformat(),
        "statuses": {c.entry.feed: c.entry.status for c in captures},
        "bytes": {c.entry.feed: c.entry.bytes for c in captures},
        "vehicle_count": vehicle_count,
        "duration_ms": round((time.monotonic() - started) * 1000),
    }
    logger.info(json.dumps(summary, sort_keys=True))
    return summary
