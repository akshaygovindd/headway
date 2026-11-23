import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fixtures import gtfs_rt as fx
from headway.config import load_city_config
from headway.ingest.capture import capture_run
from headway.ingest.fetch import FetchResult
from headway.storage import LocalStorage

NOW = datetime(2026, 6, 1, 12, 0, 5, tzinfo=UTC)
SCHEDULED = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
CONFIG = load_city_config("boston")


def ok(body: bytes) -> FetchResult:
    return FetchResult("ok", 200, body, NOW, None)


def fetcher_for(tu: FetchResult, vp: FetchResult, al: FetchResult):  # type: ignore[no-untyped-def]
    urls = {
        str(CONFIG.feeds.trip_updates): tu,
        str(CONFIG.feeds.vehicle_positions): vp,
        str(CONFIG.feeds.alerts): al,
    }
    return lambda url: urls[url]


def test_capture_run_keys_manifest_and_roundtrip(tmp_path: Path) -> None:
    tu_body, vp_body, al_body = fx.trip_updates_feed(), fx.vehicles_feed(), fx.alerts_feed()
    storage = LocalStorage(tmp_path)
    run = capture_run(
        CONFIG,
        storage,
        fetcher_for(ok(tu_body), ok(vp_body), ok(al_body)),
        lambda: NOW,
        SCHEDULED,
        offsets=(0,),
    )
    captures = run.captures
    prefix = CONFIG.storage_prefix
    h = hashlib.sha256(tu_body).hexdigest()
    cid = f"trip_updates-20260601T120005Z-{h[:8]}"
    raw_key = f"raw/{prefix}/feed=trip_updates/dt=2026-06-01/hour=12/{cid}.pb.gz"

    keys = storage.list_keys("")
    raw_keys = [k for k in keys if k.startswith("raw/")]
    manifest_keys = [k for k in keys if k.startswith("manifests/")]
    assert len(raw_keys) == 3 and raw_key in raw_keys
    assert len(manifest_keys) == 1
    run_id = captures[0].entry.run_id
    assert manifest_keys[0] == f"manifests/{prefix}/dt=2026-06-01/hour=12/20260601T1200Z.json"
    assert not run.duplicate
    assert len(run_id) == 26

    assert gzip.decompress(storage.get_bytes(raw_key)) == tu_body

    manifest = json.loads(storage.get_bytes(manifest_keys[0]))
    assert manifest["run_id"] == run_id
    assert [e["feed"] for e in manifest["entries"]] == [
        "trip_updates",
        "vehicle_positions",
        "alerts",
    ]
    e0 = manifest["entries"][0]
    assert e0["status"] == "ok" and e0["capture_id"] == cid and e0["s3_key"] == raw_key
    assert e0["content_hash"] == h and e0["bytes"] == len(tu_body)
    assert e0["entity_count"] == 1 and e0["header_parse_error"] is None
    assert e0["feed_header_ts"] == "2026-06-01T12:00:00Z"
    assert e0["city_id"] == CONFIG.city_id and e0["http_status"] == 200


def test_fetch_failure_records_error_and_stores_nothing(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    failed = FetchResult("error", 503, None, NOW, "HTTP 503")
    run = capture_run(
        CONFIG,
        storage,
        fetcher_for(ok(fx.trip_updates_feed()), failed, ok(fx.alerts_feed())),
        lambda: NOW,
        SCHEDULED,
        offsets=(0,),
    )
    captures = run.captures
    vp = captures[1].entry
    assert (vp.status, vp.http_status, vp.error) == ("error", 503, "HTTP 503")
    assert vp.s3_key is None and vp.content_hash is None
    assert captures[1].body is None
    assert captures[0].body == fx.trip_updates_feed()
    assert not [k for k in storage.list_keys("raw/") if "feed=vehicle_positions" in k]
    assert len(storage.list_keys("raw/")) == 2
    manifest = json.loads(storage.get_bytes(storage.list_keys("manifests/")[0]))
    assert len(manifest["entries"]) == 3


def test_header_parse_failure_keeps_bytes(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    run = capture_run(
        CONFIG,
        storage,
        fetcher_for(ok(fx.CORRUPT), ok(fx.vehicles_feed()), ok(fx.alerts_feed())),
        lambda: NOW,
        SCHEDULED,
        offsets=(0,),
    )
    captures = run.captures
    e = captures[0].entry
    assert e.status == "ok" and e.header_parse_error
    assert e.feed_header_ts is None and e.entity_count is None
    assert e.s3_key is not None
    assert gzip.decompress(storage.get_bytes(e.s3_key)) == fx.CORRUPT


def _run(storage: LocalStorage, at: datetime, scheduled: datetime = SCHEDULED):  # type: ignore[no-untyped-def]
    fetch = fetcher_for(ok(fx.trip_updates_feed()), ok(fx.vehicles_feed()), ok(fx.alerts_feed()))
    return capture_run(CONFIG, storage, fetch, lambda: at, scheduled, offsets=(0,))


def test_second_delivery_of_a_minute_fetches_nothing_and_writes_a_duplicate_record(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(tmp_path)
    first = _run(storage, NOW)
    raw_before = storage.list_keys("raw/")
    calls: list[str] = []

    def must_not_fetch(url: str) -> FetchResult:
        calls.append(url)
        return ok(b"")

    second = capture_run(
        CONFIG, storage, must_not_fetch, lambda: NOW.replace(microsecond=400_000), SCHEDULED
    )
    assert calls == [] and storage.list_keys("raw/") == raw_before

    prefix = CONFIG.storage_prefix
    manifests = [k for k in storage.list_keys(f"manifests/{prefix}/") if "/duplicates/" not in k]
    records = storage.list_keys(f"manifests/{prefix}/duplicates/")
    assert len(manifests) == 1 and len(records) == 1
    assert not first.duplicate and second.duplicate
    assert second.duplicate_key == records[0]
    original = json.loads(storage.get_bytes(manifests[0]))
    assert original["run_id"] == first.captures[0].entry.run_id
    assert len(original["entries"]) == 3  # the winner's manifest is untouched
    record = json.loads(storage.get_bytes(records[0]))
    assert record["run_id"] == second.captures[0].entry.run_id != original["run_id"]
    assert record["duplicate_of"] == manifests[0]
    assert [e["status"] for e in record["entries"]] == ["error"]  # one placeholder, not data


def test_the_minute_is_claimed_before_any_feed_is_fetched(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    seen: list[dict[str, object]] = []

    def fetch(url: str) -> FetchResult:
        keys = storage.list_keys("manifests/")
        seen.append(json.loads(storage.get_bytes(keys[0])))
        return ok(fx.vehicles_feed())

    capture_run(CONFIG, storage, fetch, lambda: NOW, SCHEDULED, offsets=(0,))
    assert seen and all(m["entries"] == [] for m in seen)  # claimed, not yet completed
    final = json.loads(storage.get_bytes(storage.list_keys("manifests/")[0]))
    assert len(final["entries"]) == 3


def test_a_different_minute_is_not_a_duplicate(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    _run(storage, NOW)
    later = SCHEDULED.replace(minute=1)
    assert not _run(storage, NOW.replace(minute=1), later).duplicate


def test_concurrent_deliveries_of_one_minute_fetch_once_and_leave_one_record(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    storage = LocalStorage(tmp_path)
    gate = Barrier(2)
    fetches: list[str] = []

    def deliver(i: int):  # type: ignore[no-untyped-def]
        bodies = {
            str(CONFIG.feeds.trip_updates): ok(fx.trip_updates_feed()),
            str(CONFIG.feeds.vehicle_positions): ok(fx.vehicles_feed()),
            str(CONFIG.feeds.alerts): ok(fx.alerts_feed()),
        }

        def fetch(url: str) -> FetchResult:
            fetches.append(url)
            return bodies[url]

        gate.wait(timeout=10)  # both deliveries race for the claim
        return capture_run(
            CONFIG, storage, fetch, lambda: NOW.replace(microsecond=i), SCHEDULED, offsets=(0,)
        )

    with ThreadPoolExecutor(2) as pool:
        runs = list(pool.map(deliver, [1, 2]))

    prefix = CONFIG.storage_prefix
    manifests = [k for k in storage.list_keys(f"manifests/{prefix}/") if "/duplicates/" not in k]
    records = storage.list_keys(f"manifests/{prefix}/duplicates/")
    assert len(manifests) == 1 and len(records) == 1
    assert sorted(r.duplicate for r in runs) == [False, True]
    assert len(fetches) == 3  # only the winner fetched, once per feed


class FakeTime:
    """A clock whose sleep advances it, so a 60 s minute runs instantly."""

    def __init__(self, start: datetime) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def clock(self) -> datetime:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


def _timed_fetcher(ft: FakeTime, vp_bodies: list[bytes] | None = None):  # type: ignore[no-untyped-def]
    """Fetcher stamping each result with the fake clock; each vehicle poll can differ."""
    seen = {"vp": 0}
    cost = timedelta(milliseconds=300)

    def fetch(url: str) -> FetchResult:
        ft.now += cost
        if url == str(CONFIG.feeds.vehicle_positions):
            i = seen["vp"]
            seen["vp"] += 1
            body = (vp_bodies or [fx.vehicles_feed()])[min(i, len(vp_bodies or [0]) - 1)]
        elif url == str(CONFIG.feeds.trip_updates):
            body = fx.trip_updates_feed()
        else:
            body = fx.alerts_feed()
        return FetchResult("ok", 200, body, ft.now, None)

    return fetch


def test_four_polls_per_minute_are_separate_captures_in_one_manifest(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    ft = FakeTime(SCHEDULED + timedelta(seconds=2))
    bodies = [fx.vehicles_feed(), fx.vehicles_feed(lat=42.36), fx.vehicles_feed(lat=42.37)]
    run = capture_run(
        CONFIG, storage, _timed_fetcher(ft, bodies), ft.clock, SCHEDULED, sleep=ft.sleep
    )
    entries = [c.entry for c in run.captures]
    assert [(e.feed, e.poll_index) for e in entries] == [
        ("trip_updates", 0),
        ("vehicle_positions", 0),
        ("alerts", 0),
        ("vehicle_positions", 1),
        ("vehicle_positions", 2),
        ("vehicle_positions", 3),
    ]
    assert len({e.capture_id for e in entries}) == 6
    assert {e.scheduled_at for e in entries} == {SCHEDULED}  # one capture minute
    # polls are anchored to the schedule, not chained: slots at +15, +30, +45 s
    offsets = [(e.fetched_at - SCHEDULED).total_seconds() for e in entries[3:]]
    assert [round(o) for o in offsets] == [15, 30, 45]
    manifests = [k for k in storage.list_keys("manifests/") if "/duplicates/" not in k]
    assert len(manifests) == 1
    assert [e["poll_index"] for e in json.loads(storage.get_bytes(manifests[0]))["entries"]] == [
        0,
        0,
        0,
        1,
        2,
        3,
    ]
    assert len(storage.list_keys("raw/")) == 6
    assert run.skipped_polls == ()


def test_late_polls_are_skipped_not_fetched_late(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    # the invocation starts 36 s late: poll 1 (slot 15 s) is 21 s late, poll 2 (30 s) 6 s late
    ft = FakeTime(SCHEDULED + timedelta(seconds=36))
    run = capture_run(CONFIG, storage, _timed_fetcher(ft), ft.clock, SCHEDULED, sleep=ft.sleep)
    polls = [(c.entry.feed, c.entry.poll_index) for c in run.captures]
    assert run.skipped_polls == (1, 2)
    assert polls[-1] == ("vehicle_positions", 3)
    assert len([p for p in polls if p[0] == "vehicle_positions"]) == 2


def test_poll_within_five_seconds_of_its_slot_is_taken_immediately(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    ft = FakeTime(SCHEDULED + timedelta(seconds=17))  # poll 0 ends ~18 s: poll 1 is 3 s late
    run = capture_run(CONFIG, storage, _timed_fetcher(ft), ft.clock, SCHEDULED, sleep=ft.sleep)
    assert run.skipped_polls == ()
    assert ft.sleeps and ft.sleeps[0] > 0  # waited for poll 2 and 3, not poll 1
    assert len(ft.sleeps) == 2


def test_no_poll_starts_after_the_run_deadline(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    ft = FakeTime(SCHEDULED + timedelta(seconds=44))
    # poll 3 slot is 45 s; a slow poll 0 (fetches cost 6 s each) pushes past 55 s
    cost = timedelta(seconds=6)

    def slow(url: str) -> FetchResult:
        ft.now += cost
        return FetchResult("ok", 200, fx.vehicles_feed(), ft.now, None)

    run = capture_run(CONFIG, storage, slow, ft.clock, SCHEDULED, sleep=ft.sleep)
    assert run.skipped_polls == (1, 2, 3)
    assert all(c.entry.poll_index == 0 for c in run.captures)


def test_failed_vehicle_poll_is_an_error_entry_and_other_polls_continue(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    ft = FakeTime(SCHEDULED + timedelta(seconds=2))
    good = _timed_fetcher(ft)

    def poll(url: str) -> FetchResult:
        if ft.now - SCHEDULED < timedelta(seconds=20):  # poll 1 only
            return FetchResult("error", 503, None, ft.now, "HTTP 503")
        return good(url)

    run = capture_run(CONFIG, storage, good, ft.clock, SCHEDULED, sleep=ft.sleep, poll_fetcher=poll)
    vp = [c.entry for c in run.captures if c.entry.feed == "vehicle_positions"]
    assert [e.status for e in vp] == ["ok", "error", "ok", "ok"]
    assert [e.poll_index for e in vp] == [0, 1, 2, 3]


def test_vehicle_hook_runs_after_every_vehicle_capture(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    ft = FakeTime(SCHEDULED + timedelta(seconds=2))
    seen: list[int] = []
    capture_run(
        CONFIG,
        storage,
        _timed_fetcher(ft),
        ft.clock,
        SCHEDULED,
        sleep=ft.sleep,
        on_vehicle_capture=lambda c: seen.append(c.entry.poll_index),
    )
    assert seen == [0, 1, 2, 3]
