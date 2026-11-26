"""Copy the window's curated Parquet to local disk in parallel before the S3 load (ADR-024).

The transform runs DuckDB on one thread, so reading the window through httpfs issues its S3
requests (a footer read and a few range reads per object, over a thousand objects) one after the
other: the 109 s ``s3_load`` of the 453 s run was request latency, not bytes. Here every object of
the window is fetched with one GET each, 16 at a time, into ``/tmp``; dbt then reads local files.
The window is the same ``dt >= today - lookback`` as the staging models' filter, plus the static
GTFS tables, so the staging tables are identical to a read from S3.

An object that disappears between the listing and the GET was compacted away (a part file merged
into part-compacted.parquet): its hour is listed again and fetched once more.
"""

import logging
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger()

DEFAULT_DIR = "/tmp/curated_cache"  # noqa: S108
WORKERS = 16
_CHUNK = 1 << 20
_MISSING = {"NoSuchKey", "404"}


def enabled() -> bool:
    return os.environ.get("HEADWAY_PREFETCH_CURATED", "true").lower() != "false"


def cache_dir() -> Path:
    return Path(os.environ.get("HEADWAY_CURATED_CACHE", DEFAULT_DIR))


def _list(
    s3: Any, bucket: str, prefix: str, delimiter: str | None = None
) -> tuple[list[Any], list[str]]:
    objects: list[Any] = []
    folders: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if delimiter:
            kwargs["Delimiter"] = delimiter
        if token:
            kwargs["ContinuationToken"] = token
        page = s3.list_objects_v2(**kwargs)
        objects += [(o["Key"], o["Size"]) for o in page.get("Contents", [])]
        folders += [p["Prefix"] for p in page.get("CommonPrefixes", [])]
        if not page.get("IsTruncated"):
            return objects, folders
        token = page["NextContinuationToken"]


def _get(s3: Any, bucket: str, key: str, path: Path) -> int | None:
    """Download one object with a single GET; None when it no longer exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    except s3.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] in _MISSING:
            return None
        raise
    size = 0
    with path.open("wb") as out:
        for chunk in body.iter_chunks(_CHUNK):
            out.write(chunk)
            size += len(chunk)
    return size


def prefetch(
    s3: Any, bucket: str, city: str, now: datetime, lookback_days: int, dest: Path | None = None
) -> dict[str, Any]:
    """Fetch the window to `dest` (default: the cache dir) and return what it did.

    The result's ``root`` is the directory to use as the dbt ``curated_root``: the objects are
    laid out as ``<root>/curated/<city>/...``, exactly as in the bucket.
    """
    t0 = time.monotonic()
    root = dest or cache_dir()
    shutil.rmtree(root, ignore_errors=True)  # a warm container may hold an older window
    base = f"curated/{city}/"
    _, folders = _list(s3, bucket, base, "/")
    today = now.date()
    days = [today - timedelta(days=i) for i in range(lookback_days, -2, -1)]
    prefixes = [
        f
        for folder in folders
        for f in (
            [folder] if "/table=" not in folder else [f"{folder}dt={d.isoformat()}/" for d in days]
        )
    ]
    with ThreadPoolExecutor(WORKERS) as pool:
        listed = [o for objs, _ in pool.map(lambda p: _list(s3, bucket, p), prefixes) for o in objs]
        keys = {k: size for k, size in listed if not k.endswith("/")}
        listed_ms = round((time.monotonic() - t0) * 1000)

        def fetch(key: str) -> tuple[str, int | None]:
            return key, _get(s3, bucket, key, root / key)

        results = list(pool.map(fetch, keys))
        vanished = [k for k, size in results if size is None]
        retried = 0
        if vanished:  # compacted between the listing and the GET: list those hours again
            hours = sorted({k.rsplit("/", 1)[0] + "/" for k in vanished})
            again = [o for objs, _ in pool.map(lambda p: _list(s3, bucket, p), hours) for o in objs]
            fresh = {k: s for k, s in again if k not in keys}
            for key, size in pool.map(fetch, fresh):
                if size is not None:
                    keys[key] = size
                    retried += 1
        total = sum((root / k).stat().st_size for k in keys if (root / k).exists())
    out = {
        "root": str(root),
        "objects": len(keys) - len(vanished) + retried,
        "bytes": total,
        "vanished": len(vanished),
        "retried": retried,
        "list_ms": listed_ms,
        "ms": round((time.monotonic() - t0) * 1000),
    }
    logger.info("curated prefetch: %s", out)
    return out
