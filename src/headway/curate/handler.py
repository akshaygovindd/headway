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
from headway.curate.meta import backfill_manifests
from headway.curate.pipeline import curate_new
from headway.curate.schemas import COMPACTABLE_SCHEMAS
from headway.curate.static import load_static
from headway.curate.weather import update_weather
from headway.parse.gtfs_static import GtfsSource
from headway.storage import LocalStorage, S3Storage, Storage

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DOWNLOAD_TIMEOUT_SECONDS = 120.0


def _now() -> datetime:
    return datetime.now(UTC)


def _download(url: str) -> bytes:
    local = os.environ.get("HEADWAY_GTFS_ZIP")  # image smoke test: no network
    if local:
        return Path(local).read_bytes()
    response = httpx.get(url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.content


def _storage_and_config() -> tuple[Storage, CityConfig]:
    config = load_city_config(os.environ["CITY_ID"])
    local = os.environ.get("HEADWAY_STORAGE_DIR")  # image smoke test: no S3
    if local:
        return LocalStorage(Path(local)), config
    return S3Storage(os.environ["RAW_BUCKET"]), config


def _log(summary: dict[str, Any], started: float) -> dict[str, Any]:
    summary["duration_ms"] = round((time.monotonic() - started) * 1000)
    logger.info(json.dumps(summary, sort_keys=True))
    return summary


def _compact_previous_hour(storage: Storage, config: CityConfig, now: datetime) -> dict[str, Any]:
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    rows = 0
    removed = 0
    for table in sorted(COMPACTABLE_SCHEMAS):
        result = compact_hour(storage, config, table, f"{hour:%Y-%m-%d}", f"{hour:%H}")
        rows += result.rows
        removed += result.inputs_removed
    return {"task": "compact", "hour": hour.isoformat(), "rows": rows, "inputs_removed": removed}


def curate_handler(event: dict[str, Any], context: object) -> dict[str, Any]:
    """Curate new captures.

    ``{"task": "compact"}`` compacts the previous UTC hour instead; ``{"task":
    "backfill_manifests"}`` is a one-off that builds the curated manifests and quarantine tables
    from all existing raw objects (idempotent).
    """
    started = time.monotonic()
    storage, config = _storage_and_config()
    if (event or {}).get("task") == "backfill_manifests":
        summary = {"task": "backfill_manifests", **backfill_manifests(storage, config).as_dict()}
        return _log(summary, started)
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
    weather: dict[str, Any] | None = None
    if os.environ.get("HEADWAY_ENABLE_R3", "false").lower() == "true":
        weather = update_weather(storage, config, httpx.Client(follow_redirects=True), now.date())
    summary = {
        "task": "static",
        "raw_key": raw_key,
        "bytes": len(body),
        "feed_version": result.feed_version,
        "skipped": result.skipped,
        "rows": result.rows,
    }
    if weather is not None:
        summary["weather"] = weather
    return _log(summary, started)
