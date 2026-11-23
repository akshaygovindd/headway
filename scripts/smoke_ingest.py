"""Peak-memory smoke test for the ingest handler: a time-compressed 4-poll cycle.

Run inside the Lambda Python image under `docker run --memory=128m` (see `smoke_image.sh ingest`):
the ingest package from build/lambda is mounted at /var/task. The handler's own `run_minute` runs
for a few consecutive minutes against fake feeds sized like the real ones (the largest real
bodies seen: trip updates 1.1 MB, alerts 260 KB, vehicle positions 90 KB), with an injected clock
and sleep so a 60 s minute takes a fraction of a second, and local directories standing in for S3.

Prints one JSON line with the peak resident memory of the process; exits 1 when it exceeds
--limit-mb (default 110, leaving headroom under a 128 MB function).
"""

import argparse
import json
import resource
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.transit import gtfs_realtime_pb2 as pb

from headway.config import load_city_config
from headway.ingest import handler
from headway.ingest.fetch import FetchResult
from headway.storage import LocalStorage

SCHEDULED = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
START_DELAY_S = 17.6  # median real delay from the scheduled minute to the first fetch
FETCH_S = 0.3
TARGET_BYTES = {"trip_updates": 1_150_000, "alerts": 260_000, "vehicle_positions": 90_000}


def _feed(ts: datetime) -> pb.FeedMessage:
    feed = pb.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = int(ts.timestamp())
    return feed


def _grow(feed: pb.FeedMessage, target: int, add: "object") -> bytes:
    """Add entities with ``add(feed, i)`` until the serialised feed reaches ``target`` bytes."""
    i = 0
    while True:
        for _ in range(25):
            add(feed, i)  # type: ignore[operator]
            i += 1
        body = feed.SerializeToString()
        if len(body) >= target:
            return body


def trip_updates(ts: datetime) -> bytes:
    def add(feed: pb.FeedMessage, i: int) -> None:
        e = feed.entity.add()
        e.id = f"tu{i}"
        tu = e.trip_update
        tu.trip.trip_id = f"trip-{i:06d}"
        tu.trip.route_id = ("Red", "Orange", "Blue", "Green-B")[i % 4]
        tu.trip.start_date = "20260601"
        tu.trip.start_time = "11:50:00"
        tu.vehicle.id = f"y{i}"
        for seq in range(1, 22):
            stu = tu.stop_time_update.add()
            stu.stop_id = f"7{seq:04d}"
            stu.stop_sequence = seq * 10
            stu.arrival.time = int(ts.timestamp()) + seq * 120
            stu.arrival.uncertainty = 30

    return _grow(_feed(ts), TARGET_BYTES["trip_updates"], add)


def alerts(ts: datetime) -> bytes:
    def add(feed: pb.FeedMessage, i: int) -> None:
        e = feed.entity.add()
        e.id = f"al{i}"
        a = e.alert
        a.effect = pb.Alert.SIGNIFICANT_DELAYS
        for k in range(6):
            ie = a.informed_entity.add()
            ie.route_id = ("Red", "Orange", "Blue", "Green-B")[k % 4]
            ie.stop_id = f"7{(i + k) % 900:04d}"
        t = a.header_text.translation.add()
        t.text = f"Delays of about {i % 30} minutes on this line because of a signal problem"
        t.language = "en"

    return _grow(_feed(ts), TARGET_BYTES["alerts"], add)


def vehicles(ts: datetime, jitter: int) -> bytes:
    def add(feed: pb.FeedMessage, i: int) -> None:
        e = feed.entity.add()
        e.id = f"v{i}"
        v = e.vehicle
        v.vehicle.id = f"y{i}"
        v.trip.trip_id = f"trip-{i:06d}"
        v.trip.route_id = ("Red", "Orange", "Blue", "Green-B")[i % 4]
        v.position.latitude = 42.30 + (i % 50) / 1000 + jitter / 100_000
        v.position.longitude = -71.10 + (i % 40) / 1000
        v.position.bearing = 90.0
        v.current_status = pb.VehiclePosition.IN_TRANSIT_TO
        v.stop_id = f"7{i % 900:04d}"
        v.current_stop_sequence = 3
        v.timestamp = int(ts.timestamp()) - 5

    return _grow(_feed(ts), TARGET_BYTES["vehicle_positions"], add)


@dataclass
class FakeTime:
    now: datetime

    def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def peak_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # KB on Linux


def cgroup_peak_mb() -> float | None:
    for path in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
        try:
            return int(Path(path).read_text()) / 1_048_576
        except (OSError, ValueError):
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True, help="directory standing in for S3")
    ap.add_argument("--minutes", type=int, default=3)
    ap.add_argument("--polls", type=int, default=4, choices=[1, 4], help="VEHICLE_POLLS")
    ap.add_argument("--limit-mb", type=float, default=110.0)
    args = ap.parse_args()

    config = load_city_config("boston")
    raw = LocalStorage(args.out / "raw")
    public = LocalStorage(args.out / "public")
    urls = {
        str(config.feeds.trip_updates): "trip_updates",
        str(config.feeds.alerts): "alerts",
        str(config.feeds.vehicle_positions): "vehicle_positions",
    }
    baseline = peak_mb()
    per_minute = []
    sizes: dict[str, int] = {}
    for minute in range(args.minutes):
        scheduled = SCHEDULED + timedelta(minutes=minute)
        ft = FakeTime(scheduled + timedelta(seconds=START_DELAY_S))
        polls = {"vehicle_positions": 0}

        def fetch(url: str, ft: FakeTime = ft, polls: dict[str, int] = polls) -> FetchResult:
            ft.now += timedelta(seconds=FETCH_S)
            feed = urls[url]
            if feed == "trip_updates":
                body = trip_updates(ft.now)
            elif feed == "alerts":
                body = alerts(ft.now)
            else:
                polls[feed] += 1
                body = vehicles(ft.now, polls[feed])
            sizes[feed] = max(sizes.get(feed, 0), len(body))
            return FetchResult("ok", 200, body, ft.now, None)

        summary = handler.run_minute(
            config,
            raw,
            public,
            scheduled,
            fetcher=fetch,
            poll_fetcher=fetch,
            clock=lambda ft=ft: ft.now,
            sleep=ft.sleep,
            polls=args.polls,
        )
        per_minute.append(
            {
                "minute": minute,
                "vehicle_polls": summary["vehicle_polls"],
                "skipped_polls": summary["skipped_polls"],
                "vehicles_in_snapshot": summary["vehicle_count"],
                "peak_rss_mb": round(peak_mb(), 1),
            }
        )
    result = {
        "peak_rss_mb": round(peak_mb(), 1),
        "baseline_after_imports_mb": round(baseline, 1),
        "cgroup_peak_mb": None if (c := cgroup_peak_mb()) is None else round(c, 1),
        "polls": args.polls,
        "limit_mb": args.limit_mb,
        "body_bytes": sizes,
        "raw_objects": len(raw.list_keys("raw/")),
        "manifests": len(raw.list_keys("manifests/")),
        "minutes": per_minute,
    }
    print(json.dumps(result))
    ok = result["peak_rss_mb"] <= args.limit_mb and all(
        m["vehicle_polls"] == ["ok"] * args.polls for m in per_minute
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
