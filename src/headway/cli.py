"""Command-line interface."""

import argparse
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from headway.config import CityConfig, load_city_config
from headway.contracts.manifest import ManifestEntry
from headway.ingest.capture import Clock, Fetcher, capture_run
from headway.ingest.fetch import fetch_feed
from headway.samples.build import build_sample
from headway.storage import LocalStorage, Storage


def _utcnow() -> datetime:
    return datetime.now(UTC)


def next_minute(now: datetime) -> datetime:
    """The first minute boundary at or after now."""
    floor = now.replace(second=0, microsecond=0)
    return floor if floor == now else floor + timedelta(minutes=1)


def format_run(scheduled_at: datetime, entries: Sequence[ManifestEntry]) -> str:
    statuses = " ".join(f"{e.feed}={e.status}" for e in entries)
    total = sum(e.bytes or 0 for e in entries)
    return f"{scheduled_at:%Y-%m-%dT%H:%M:%SZ} {statuses} bytes={total}"


def run_captures(
    config: CityConfig,
    storage: Storage,
    fetcher: Fetcher,
    minutes: int,
    clock: Clock = _utcnow,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[str], None] = print,
) -> None:
    """Run capture_run once per minute boundary, `minutes` times."""
    scheduled_at = next_minute(clock())
    for _ in range(minutes):
        wait = (scheduled_at - clock()).total_seconds()
        if wait > 0:
            sleep(wait)
        captures = capture_run(config, storage, fetcher, clock, scheduled_at)
        emit(format_run(scheduled_at, [c.entry for c in captures]))
        scheduled_at += timedelta(minutes=1)


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return n


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="headway")
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="capture realtime feeds once per minute")
    cap.add_argument("--city", required=True, help="city id, e.g. boston")
    cap.add_argument("--minutes", type=_positive_int, required=True, help="number of runs")
    cap.add_argument("--out", type=Path, required=True, help="output directory")
    samples = sub.add_parser("samples", help="sample dataset tools")
    samples_sub = samples.add_subparsers(dest="samples_command", required=True)
    build = samples_sub.add_parser("build", help="build the subway sample from a real capture")
    build.add_argument("--source", type=Path, required=True, help="capture directory")
    build.add_argument("--gtfs", type=Path, required=True, help="GTFS zip or folder")
    build.add_argument("--out", type=Path, required=True, help="output directory")
    build.add_argument("--city", required=True, help="city id, e.g. boston")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_city_config(args.city)
    if args.command == "samples":
        counts = build_sample(config, args.source, args.gtfs, args.out)
        print(" ".join(f"{k}={v}" for k, v in counts.items()))
        return 0
    with httpx.Client(follow_redirects=True) as client:
        run_captures(
            config,
            LocalStorage(args.out),
            lambda url: fetch_feed(url, client),
            args.minutes,
        )
    return 0
