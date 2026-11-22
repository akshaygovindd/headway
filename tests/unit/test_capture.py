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
    captures = capture_run(
        CONFIG,
        storage,
        fetcher_for(ok(tu_body), ok(vp_body), ok(al_body)),
        lambda: NOW,
        SCHEDULED,
    )
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
    assert manifest_keys[0] == f"manifests/{prefix}/dt=2026-06-01/hour=12/{run_id}.json"
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
    captures = capture_run(
        CONFIG,
        storage,
        fetcher_for(ok(fx.trip_updates_feed()), failed, ok(fx.alerts_feed())),
        lambda: NOW,
        SCHEDULED,
    )
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
    captures = capture_run(
        CONFIG,
        storage,
        fetcher_for(ok(fx.CORRUPT), ok(fx.vehicles_feed()), ok(fx.alerts_feed())),
        lambda: NOW,
        SCHEDULED,
    )
    e = captures[0].entry
    assert e.status == "ok" and e.header_parse_error
    assert e.feed_header_ts is None and e.entity_count is None
    assert e.s3_key is not None
    assert gzip.decompress(storage.get_bytes(e.s3_key)) == fx.CORRUPT
