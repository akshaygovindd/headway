import gzip
import json
from pathlib import Path

import pyarrow.parquet as pq

from fixtures import gtfs_rt as fx
from fixtures.rows import FETCHED_AT
from headway.config import load_city_config
from headway.contracts.manifest import ManifestEntry
from headway.curate.normalize import batch_id_for, normalize_batch
from headway.storage import LocalStorage

CONFIG = load_city_config("boston")


def put_capture(
    storage: LocalStorage, capture_id: str, body: bytes, feed: str = "trip_updates"
) -> ManifestEntry:
    key = f"raw/boston/feed={feed}/{capture_id}.pb.gz"
    storage.put_bytes(key, gzip.compress(body, mtime=0))
    return ManifestEntry(
        run_id="run1",
        capture_id=capture_id,
        feed=feed,  # type: ignore[arg-type]
        city_id="boston",
        status="ok",
        http_status=200,
        scheduled_at=FETCHED_AT,
        fetched_at=FETCHED_AT,
        feed_header_ts=None,
        entity_count=1,
        bytes=len(body),
        content_hash=f"h-{capture_id}",
        s3_key=key,
    )


def test_batch_id_is_order_and_duplicate_independent() -> None:
    assert batch_id_for(["b", "a"]) == batch_id_for(["a", "b", "a"])
    assert batch_id_for(["a"]) != batch_id_for(["a", "b"])
    assert len(batch_id_for(["a"])) == 16


def test_unknown_route_is_quarantined(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "c1", fx.trip_updates_feed())
    result = normalize_batch(storage, CONFIG, [entry], dim_routes={"Blue"})
    assert result.rows == {}
    assert result.quarantined == {"trip_updates": {"unknown_route": 2}}
    keys = [k for k in storage.list_keys("quarantine/") if k.endswith("c1.jsonl")]
    assert len(keys) == 1
    first = json.loads(storage.get_bytes(keys[0]).splitlines()[0])
    assert first["reason"] == "unknown_route" and first["capture_id"] == "c1"


def test_known_route_is_written_with_lineage(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "c1", fx.trip_updates_feed())
    result = normalize_batch(storage, CONFIG, [entry], dim_routes={"Red"})
    assert result.rows == {"trip_updates": 2}
    (key,) = [k for k in result.keys if k.startswith("curated/")]
    assert f"part-{result.batch_id}.parquet" in key
    table = pq.read_table(tmp_path / key)
    assert table.num_rows == 2
    assert set(table.column("content_hash").to_pylist()) == {"h-c1"}
    assert set(table.column("run_id").to_pylist()) == {"run1"}


def test_corrupt_capture_is_quarantined_not_lost(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "bad", b"\xff\xfenot a protobuf")
    result = normalize_batch(storage, CONFIG, [entry], dim_routes={"Red"})
    assert result.quarantined == {"trip_updates": {"protobuf_parse_error": 1}}
    assert result.rows == {}
    assert storage.exists(entry.s3_key or "")


def test_duplicate_content_kept_but_repeated_capture_id_written_once(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    body = fx.trip_updates_feed()
    a = put_capture(storage, "c1", body)
    b = put_capture(storage, "c2", body)
    result = normalize_batch(storage, CONFIG, [a, b, a], dim_routes={"Red"})
    assert result.captures == 2
    assert result.rows == {"trip_updates": 4}


def test_rerun_is_byte_identical(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entries = [put_capture(storage, c, fx.trip_updates_feed()) for c in ("c1", "c2")]
    first = normalize_batch(storage, CONFIG, entries, dim_routes={"Red"})
    before = {k: storage.get_bytes(k) for k in first.keys}
    second = normalize_batch(storage, CONFIG, list(reversed(entries)), dim_routes={"Red"})
    assert second.keys == first.keys
    assert {k: storage.get_bytes(k) for k in second.keys} == before
