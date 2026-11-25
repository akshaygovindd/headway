"""Warm-container copy of the small gold tables the API reads (ADR-023).

The tables are downloaded to the cache directory once per export, keyed by the manifest's
`exported_at`; the manifest is re-read at most every `recheck_s` seconds. Queries run on the local
copy. Any failure (download, disk, a query on the copy) falls back to HTTPS reads of the public
objects, so the cache can only make a response faster, never different or unavailable.
"""

import json
import logging
import os
import re
import shutil
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from headway_mcp import config, runner

log = logging.getLogger("headway_api.cache")


@dataclass(frozen=True)
class Snapshot:
    base: str  # where queries read: the local copy or the remote root
    remote: str  # the public data root (shown in provenance)
    manifest: dict[str, Any]
    served_from: str  # "cache" or "remote"


class GoldSource:
    def __init__(
        self,
        remote: str,
        cache_dir: str | None,
        tables: Iterable[str],
        recheck_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.remote, self.cache_dir = remote, cache_dir
        self.tables, self.recheck_s, self.clock = tuple(tables), recheck_s, clock
        self._lock = threading.Lock()
        self._snap: Snapshot | None = None
        self._checked = 0.0
        self._local: str | None = None
        self._local_exported: str | None = None

    def snapshot(self) -> Snapshot:
        with self._lock:
            now = self.clock()
            if self._snap is not None and now - self._checked < self.recheck_s:
                return self._snap
            try:
                manifest = runner.read_json(self.remote, "gold/manifest.json")
            except runner.DataError:
                if self._snap is not None:
                    self._checked = now  # keep serving the last known export, retry later
                    return self._snap
                raise
            self._checked = now
            exported = str(manifest.get("exported_at"))
            local = self._local if self._local_exported == exported else None
            if local is None and self.cache_dir:
                try:
                    local = self._download(manifest, exported)
                except Exception as exc:  # noqa: BLE001 (any failure falls back to HTTPS)
                    log.warning("gold cache download failed: %s", type(exc).__name__)
                    local = None
                self._local, self._local_exported = local, exported if local else None
            self._snap = Snapshot(local or self.remote, self.remote, manifest,
                                  "cache" if local else "remote")  # fmt: skip
            return self._snap

    def fallback(self) -> None:
        """A query on the local copy failed: read over HTTPS until the next re-check."""
        with self._lock:
            if self._local:  # drop the suspect copy so the next re-check downloads it afresh
                shutil.rmtree(self._local[len("file://") :], ignore_errors=True)
            self._local = self._local_exported = None
            if self._snap is not None:
                self._snap = Snapshot(self.remote, self.remote, self._snap.manifest, "remote")

    def _download(self, manifest: dict[str, Any], exported: str) -> str:
        assert self.cache_dir
        key = re.sub(r"[^0-9A-Za-z_-]", "", exported) or "export"
        final = os.path.join(self.cache_dir, key)
        if os.path.isdir(final):
            return f"file://{final}"
        os.makedirs(self.cache_dir, exist_ok=True)
        tmp = os.path.join(self.cache_dir, f".tmp-{os.getpid()}-{key}")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(os.path.join(tmp, "gold"))
        try:
            present = set(manifest.get("row_counts", {}))
            for table in self.tables:
                if table not in present:
                    continue
                url = f"{self.remote}/gold/{table}.parquet"
                with (
                    urllib.request.urlopen(url, timeout=config.HTTP_TIMEOUT_S * 3) as resp,  # noqa: S310
                    open(os.path.join(tmp, "gold", f"{table}.parquet"), "wb") as out,
                ):
                    shutil.copyfileobj(resp, out)
            with open(os.path.join(tmp, "gold", "manifest.json"), "w") as f:
                json.dump(manifest, f)
            os.replace(tmp, final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        for name in os.listdir(self.cache_dir):  # drop the older exports
            if name != key:
                shutil.rmtree(os.path.join(self.cache_dir, name), ignore_errors=True)
        return f"file://{final}"
