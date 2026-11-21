from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from fixtures import gtfs_rt as fx
from headway.cli import build_parser, next_minute, run_captures
from headway.config import load_city_config
from headway.ingest.fetch import FetchResult
from headway.storage import LocalStorage


def test_next_minute() -> None:
    t = datetime(2026, 6, 1, 12, 0, 20, tzinfo=UTC)
    assert next_minute(t) == datetime(2026, 6, 1, 12, 1, tzinfo=UTC)
    on = datetime(2026, 6, 1, 12, 1, tzinfo=UTC)
    assert next_minute(on) == on


def test_parser() -> None:
    a = build_parser().parse_args(["capture", "--city", "boston", "--minutes", "3", "--out", "x"])
    assert (a.city, a.minutes, a.out) == ("boston", 3, Path("x"))
    with pytest.raises(SystemExit):
        build_parser().parse_args(["capture", "--city", "boston", "--minutes", "0", "--out", "x"])


def test_run_captures_aligns_and_prints(tmp_path: Path) -> None:
    now = [datetime(2026, 6, 1, 12, 0, 20, tzinfo=UTC)]
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        now[0] += timedelta(seconds=s)

    bodies = [fx.trip_updates_feed(), fx.vehicles_feed(), fx.alerts_feed()]
    config = load_city_config("boston")
    by_url = {
        str(config.feeds.trip_updates): bodies[0],
        str(config.feeds.vehicle_positions): bodies[1],
        str(config.feeds.alerts): bodies[2],
    }

    def fetcher(url: str) -> FetchResult:
        return FetchResult("ok", 200, by_url[url], now[0], None)

    lines: list[str] = []
    run_captures(config, LocalStorage(tmp_path), fetcher, 2, lambda: now[0], sleep, lines.append)
    assert sleeps == [40.0, 60.0]
    assert len(lines) == 2
    total = sum(len(b) for b in bodies)
    assert lines[0] == (
        f"2026-06-01T12:01:00Z trip_updates=ok vehicle_positions=ok alerts=ok bytes={total}"
    )
    assert lines[1].startswith("2026-06-01T12:02:00Z")
    assert len(LocalStorage(tmp_path).list_keys("manifests/")) == 2
