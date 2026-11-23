import gzip
import hashlib
import json
from datetime import UTC, datetime
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
    )
    captures = run.captures
    e = captures[0].entry
    assert e.status == "ok" and e.header_parse_error
    assert e.feed_header_ts is None and e.entity_count is None
    assert e.s3_key is not None
    assert gzip.decompress(storage.get_bytes(e.s3_key)) == fx.CORRUPT


def _run(storage: LocalStorage, at: datetime, scheduled: datetime = SCHEDULED):  # type: ignore[no-untyped-def]
    fetch = fetcher_for(ok(fx.trip_updates_feed()), ok(fx.vehicles_feed()), ok(fx.alerts_feed()))
    return capture_run(CONFIG, storage, fetch, lambda: at, scheduled)


def test_second_delivery_of_a_minute_writes_a_duplicate_record_not_a_manifest(
    tmp_path: Path,
) -> None:
    storage = LocalStorage(tmp_path)
    first = _run(storage, NOW)
    second = _run(storage, NOW.replace(microsecond=400_000))

    prefix = CONFIG.storage_prefix
    manifests = [k for k in storage.list_keys(f"manifests/{prefix}/") if "/duplicates/" not in k]
    records = storage.list_keys(f"manifests/{prefix}/duplicates/")
    assert len(manifests) == 1 and len(records) == 1
    assert not first.duplicate and second.duplicate
    assert second.duplicate_key == records[0]
    original = json.loads(storage.get_bytes(manifests[0]))
    assert original["run_id"] == first.captures[0].entry.run_id
    record = json.loads(storage.get_bytes(records[0]))
    assert record["run_id"] == second.captures[0].entry.run_id != original["run_id"]
    assert record["duplicate_of"] == manifests[0]
    assert len(record["entries"]) == 3


def test_a_different_minute_is_not_a_duplicate(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    _run(storage, NOW)
    later = SCHEDULED.replace(minute=1)
    assert not _run(storage, NOW.replace(minute=1), later).duplicate


def test_concurrent_deliveries_of_one_minute_give_one_manifest_and_one_record(
    tmp_path: Path,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    storage = LocalStorage(tmp_path)
    gate = Barrier(2)

    def deliver(i: int):  # type: ignore[no-untyped-def]
        bodies = {
            str(CONFIG.feeds.trip_updates): ok(fx.trip_updates_feed()),
            str(CONFIG.feeds.vehicle_positions): ok(fx.vehicles_feed()),
            str(CONFIG.feeds.alerts): ok(fx.alerts_feed()),
        }

        def fetch(url: str) -> FetchResult:
            if url == str(CONFIG.feeds.alerts):
                gate.wait(timeout=10)  # both deliveries finish fetching, then race for the claim
            return bodies[url]

        return capture_run(CONFIG, storage, fetch, lambda: NOW.replace(microsecond=i), SCHEDULED)

    with ThreadPoolExecutor(2) as pool:
        runs = list(pool.map(deliver, [1, 2]))

    prefix = CONFIG.storage_prefix
    manifests = [k for k in storage.list_keys(f"manifests/{prefix}/") if "/duplicates/" not in k]
    records = storage.list_keys(f"manifests/{prefix}/duplicates/")
    assert len(manifests) == 1 and len(records) == 1
    assert sorted(r.duplicate for r in runs) == [False, True]
