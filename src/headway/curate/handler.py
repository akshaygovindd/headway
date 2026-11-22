"""AWS Lambda entry points for curation (every 15 minutes, hourly compaction) and static GTFS."""

import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from headway.config import CityConfig, load_city_config
from headway.curate.compact import compact_hour
from headway.curate.pipeline import curate_new
from headway.curate.schemas import REALTIME_SCHEMAS
from headway.curate.static import load_static
from headway.parse.gtfs_static import GtfsSource
from headway.storage import S3Storage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DOWNLOAD_TIMEOUT_SECONDS = 120.0


def _now() -> datetime:
    return datetime.now(UTC)


def _download(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def _storage_and_config() -> tuple[S3Storage, CityConfig]:
    return S3Storage(os.environ["RAW_BUCKET"]), load_city_config(os.environ["CITY_ID"])


def _log(summary: dict[str, Any], started: float) -> dict[str, Any]:
    summary["duration_ms"] = round((time.monotonic() - started) * 1000)
    logger.info(json.dumps(summary, sort_keys=True))
    return summary


def _compact_previous_hour(storage: S3Storage, config: CityConfig, now: datetime) -> dict[str, Any]:
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    rows = 0
    removed = 0
    for table in sorted(REALTIME_SCHEMAS):
        result = compact_hour(storage, config, table, f"{hour:%Y-%m-%d}", f"{hour:%H}")
        rows += result.rows
        removed += result.inputs_removed
    return {"task": "compact", "hour": hour.isoformat(), "rows": rows, "inputs_removed": removed}


def curate_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Curate new captures; with ``{"task": "compact"}`` compact the previous UTC hour instead."""
    started = time.monotonic()
    storage, config = _storage_and_config()
    if (event or {}).get("task") == "compact":
        return _log(_compact_previous_hour(storage, config, _now()), started)
    result = curate_new(storage, config)
    summary = {
        "task": "curate",
        "batches": len(result.batches),
        "captures": sum(b.captures for b in result.batches),
        "rows": result.rows(),
        "quarantined": result.quarantined(),
    }
    return _log(summary, started)


def static_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Download the full GTFS zip, keep it as raw, and load it if its feed_version is new."""
    started = time.monotonic()
    storage, config = _storage_and_config()
    body = _download(str(config.feeds.gtfs_static))
    now = _now()
    raw_key = (
        f"raw/{config.storage_prefix}/feed=gtfs_static/dt={now:%Y-%m-%d}/hour={now:%H}/"
        f"gtfs-{now:%Y%m%dT%H%M%SZ}.zip"
    )
    storage.put_bytes(raw_key, body, content_type="application/zip")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "gtfs.zip"
        path.write_bytes(body)
        result = load_static(storage, config, GtfsSource(path))
    summary = {
        "task": "static",
        "raw_key": raw_key,
        "bytes": len(body),
        "feed_version": result.feed_version,
        "skipped": result.skipped,
        "rows": result.rows,
    }
    return _log(summary, started)
