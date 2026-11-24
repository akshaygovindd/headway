"""A replayable pipeline world for failure simulations (no AWS).

The world starts from the real sample capture (samples/boston, 14:36-15:35 UTC) and fills the
missing minutes of 14:00-16:59 UTC with captures written by the real ingest code
(`run_minute` over a local store), so three hours of 2026-11-22 (09:00-11:59 local) are
complete; the last one is the recovery hour.
A scenario then edits minutes (drops them, records them as failed fetches, delivers them twice,
bends a feed) before the world is curated with the real curation code and every dbt model is
built over it. Nothing here talks to AWS.
"""

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
from google.transit import gtfs_realtime_pb2 as pb

from headway.config import load_city_config
from headway.curate.replay import replay
from headway.curate.static import GtfsSource, load_static
from headway.ingest import handler
from headway.ingest.capture import FEEDS, manifest_key
from headway.ingest.fetch import FetchResult
from headway.ingest.handler import run_minute
from headway.storage import LocalStorage

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "boston"
CONFIG = load_city_config("boston")
CITY = CONFIG.storage_prefix
START = datetime(2026, 11, 22, 14, 0, tzinfo=UTC)
MINUTES = 180  # 14:00-16:59 UTC, 09:00-11:59 local
DBT = ["uv", "run", "--project", "dbt", "dbt"]
DBT_ARGS = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "local"]

Fetcher = Callable[[str], FetchResult]


def minute_range(start: datetime, n: int) -> list[datetime]:
    return [start + timedelta(minutes=i) for i in range(n)]


def at(hour: int, minute: int) -> datetime:
    return START.replace(hour=hour, minute=minute)


def _vary(body: bytes, seconds: int) -> bytes:
    """The same feed with a later header timestamp: a distinct payload, so not a duplicate."""
    feed = pb.FeedMessage()
    feed.ParseFromString(body)
    feed.header.timestamp += seconds
    return feed.SerializeToString()


class World:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.source = tmp / "source"
        shutil.copytree(SAMPLE, self.source)
        self.store = LocalStorage(self.source)
        self.public = LocalStorage(tmp / "public")
        self.real: dict[str, dict[datetime, bytes]] = {f: {} for f in FEEDS}
        self._index_sample()
        self.filled: list[datetime] = []
        self.fill()

    # -- building the base world -------------------------------------------------------------

    def _manifests(self) -> list[tuple[str, dict[str, Any]]]:
        out = []
        for key in self.store.list_keys(f"manifests/{CITY}/"):
            out.append((key, json.loads(self.store.get_bytes(key))))
        return out

    def _index_sample(self) -> None:
        import gzip

        for _, doc in self._manifests():
            for e in doc.get("entries", []):
                if e["status"] == "ok" and e.get("poll_index", 0) == 0 and e["s3_key"]:
                    minute = datetime.fromisoformat(e["scheduled_at"].replace("Z", "+00:00"))
                    body = gzip.decompress(self.store.get_bytes(e["s3_key"]))
                    self.real[e["feed"]][minute] = body

    def body(self, feed: str, minute: datetime) -> bytes:
        """The real capture of the minute, else the nearest real one with a distinct header."""
        known = self.real[feed]
        if minute in known:
            return known[minute]
        nearest = min(known, key=lambda m: abs((m - minute).total_seconds()))
        return _vary(known[nearest], int((minute - nearest).total_seconds()))

    def fetcher(self, minute: datetime) -> Fetcher:
        urls = {
            str(CONFIG.feeds.trip_updates): "trip_updates",
            str(CONFIG.feeds.vehicle_positions): "vehicle_positions",
            str(CONFIG.feeds.alerts): "alerts",
        }

        def fetch(url: str) -> FetchResult:
            body = self.body(urls[url], minute)
            return FetchResult("ok", 200, body, minute + timedelta(seconds=5), None)

        return fetch

    def _sample_minutes(self) -> set[datetime]:
        return {m for per_feed in self.real.values() for m in per_feed}

    def run(self, minute: datetime, fetcher: Fetcher | None = None) -> dict[str, Any]:
        """One real ingest run for the scheduled minute (a second call is a duplicate delivery)."""
        fetch = fetcher or self.fetcher(minute)
        return run_minute(
            CONFIG, self.store, self.public, minute,
            fetcher=fetch, poll_fetcher=fetch,
            clock=lambda: minute + timedelta(seconds=5), sleep=lambda s: None, polls=1,
        )  # fmt: skip

    def extend(self, start: datetime, n: int) -> None:
        """Ingest more minutes outside the base window (for example another service date)."""
        for minute in minute_range(start, n):
            self.run(minute)

    def fill(self) -> None:
        """Run ingest for every minute of the window the sample does not have."""
        have = self._sample_minutes()
        for minute in minute_range(START, MINUTES):
            if minute not in have:
                self.run(minute)
                self.filled.append(minute)

    # -- scenario edits ----------------------------------------------------------------------

    def drop(self, minutes: Iterable[datetime]) -> None:
        """Remove the minutes' runs entirely: nothing was captured and no manifest exists."""
        wanted = {m.replace(second=0, microsecond=0) for m in minutes}
        for key, doc in self._manifests():
            entries = doc.get("entries", [])
            if not entries:
                continue
            scheduled = datetime.fromisoformat(entries[0]["scheduled_at"].replace("Z", "+00:00"))
            if scheduled.replace(second=0, microsecond=0) in wanted:
                for e in entries:
                    if e.get("s3_key"):
                        (self.source / e["s3_key"]).unlink(missing_ok=True)
                (self.source / key).unlink()
        for m in wanted:
            self.real_forget(m)

    def real_forget(self, minute: datetime) -> None:
        for per_feed in self.real.values():
            per_feed.pop(minute, None)

    def replace(self, minutes: Iterable[datetime], fetcher: Fetcher | None = None) -> None:
        """Drop the minutes and run ingest again, optionally with another fetcher."""
        minutes = list(minutes)
        self.drop(minutes)
        for minute in minutes:
            self.run(minute, fetcher)

    def feed_down(self, minutes: Iterable[datetime]) -> None:
        """The feed_down drill: ingest records every feed as an error, fetching nothing."""
        minutes = list(minutes)
        self.drop(minutes)
        for minute in minutes:
            # the drill's own fetcher, stamped by the replayed clock like every other capture
            stamped = minute + timedelta(seconds=5)
            with patch.object(handler, "_now", lambda stamped=stamped: stamped):
                self.run(minute, handler._chaos_fetch)

    def manifest_keys(self, minute: datetime) -> list[str]:
        stamp = minute.strftime("%Y%m%dT%H%MZ")
        return [k for k in self.store.list_keys(f"manifests/{CITY}/") if stamp in k]

    def claimed(self, minute: datetime) -> bool:
        return self.store.exists(manifest_key(CONFIG, minute))

    # -- curating and building ---------------------------------------------------------------

    def curate(self, extra_static: Path | None = None) -> Path:
        target = self.tmp / "replay"
        replay(self.source, target, CONFIG, compact=True)
        if extra_static is not None:
            load_static(LocalStorage(target), CONFIG, GtfsSource(extra_static))
        self.curated = target
        return target

    def build(self, **variables: Any) -> "Result":
        db = self.tmp / "world.duckdb"
        env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
        env["HEADWAY_DUCKDB_PATH"] = str(db)
        vars_ = json.dumps({"curated_root": str(self.curated), **variables})
        subprocess.run(
            [*DBT, "build", *DBT_ARGS, "--vars", vars_,
             "--exclude", "resource_type:test", "resource_type:unit_test"],
            cwd=ROOT, env=env, check=True, stdout=subprocess.DEVNULL,
        )  # fmt: skip
        return Result(db)


class Result:
    """Read-only access to the built world."""

    def __init__(self, db: Path) -> None:
        self.db = db

    def rows(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        """Rows as dicts; timestamps with a zone come back as UTC text (no pytz needed)."""
        con = duckdb.connect(str(self.db), read_only=True)
        try:
            con.execute("set TimeZone = 'UTC'")
            rel = con.sql(sql, params=list(params))
            cols = [
                f'cast("{c}" as varchar) as "{c}"' if "TIME ZONE" in str(t) else f'"{c}"'
                for c, t in zip(rel.columns, rel.types, strict=True)
            ]
            return [
                dict(zip(rel.columns, r, strict=True))
                for r in rel.project(", ".join(cols)).fetchall()
            ]
        finally:
            con.close()

    def one(self, sql: str, *params: Any) -> dict[str, Any]:
        (row,) = self.rows(sql, *params)
        return row

    def check(self, name: str, hour: int) -> dict[str, Any]:
        return self.one(
            "select value, status from agg_quality_checks_hourly "
            "where check_name = ? and service_date = date '2026-11-22' and hour_local = ?",
            name, hour,
        )  # fmt: skip

    def flags(self, hour: int) -> dict[str, Any]:
        return self.one(
            "select * from int_hour_flags "
            "where service_date = date '2026-11-22' and hour_local = ?",
            hour,
        )
