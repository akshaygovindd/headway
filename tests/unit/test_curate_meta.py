import io
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from test_curate_normalize import put_capture

from fixtures import gtfs_rt as fx
from fixtures.rows import FETCHED_AT
from headway.config import load_city_config
from headway.curate.compact import COMPACTED, compact_hour
from headway.curate.meta import backfill_manifests, write_manifest_parts
from headway.curate.normalize import normalize_batch
from headway.curate.schemas import META_SCHEMAS
from headway.storage import LocalStorage

CONFIG = load_city_config("boston")
ROUTES = {"Red": 1}
DT, HOUR = f"{FETCHED_AT:%Y-%m-%d}", f"{FETCHED_AT:%H}"
MANIFESTS = f"curated/boston/table=manifests/dt={DT}/hour={HOUR}/"
QUARANTINE = f"curated/boston/table=quarantine/dt={DT}/hour={HOUR}/"


def _rows(storage: LocalStorage, base: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for key in storage.list_keys(base):
        rows.extend(pq.read_table(io.BytesIO(storage.get_bytes(key))).to_pylist())
    return rows


def _raw_manifest(storage: LocalStorage, run_id: str, entries: list[object]) -> None:
    doc = {"run_id": run_id, "entries": [e.model_dump(mode="json") for e in entries]}  # type: ignore[attr-defined]
    storage.put_bytes(
        f"manifests/boston/dt={DT}/hour={HOUR}/{run_id}.json", json.dumps(doc).encode()
    )


def test_manifest_parts_have_the_explicit_schema_and_are_deterministic(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "c1", fx.trip_updates_feed())
    keys = write_manifest_parts(storage, CONFIG, [entry, entry])
    assert len(keys) == 1 and keys[0].startswith(MANIFESTS + "part-")
    assert pq.read_schema(tmp_path / keys[0]) == META_SCHEMAS["manifests"].remove_metadata()
    ts = pq.read_schema(tmp_path / keys[0]).field("fetched_at").type
    assert ts == pa.timestamp("us", tz="UTC")
    rows = _rows(storage, MANIFESTS)
    assert [r["capture_id"] for r in rows] == ["c1"]
    assert write_manifest_parts(storage, CONFIG, [entry]) == keys
    assert len(storage.list_keys(MANIFESTS)) == 1


def test_normalize_writes_quarantine_rows(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "c1", fx.trip_updates_feed())
    normalize_batch(storage, CONFIG, [entry], dim_routes={})  # every route is unknown
    rows = _rows(storage, QUARANTINE)
    assert rows
    assert {r["reason"] for r in rows} == {"unknown_route"}
    assert {r["source_table"] for r in rows} == {"trip_updates"}
    assert sorted(r["line_no"] for r in rows) == list(range(len(rows)))  # type: ignore[type-var]
    raw = storage.get_bytes(f"quarantine/boston/table=trip_updates/dt={DT}/c1.jsonl")
    assert len(raw.splitlines()) == len(rows)


def test_compaction_merges_manifest_parts(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    for cid in ("c1", "c2"):
        write_manifest_parts(storage, CONFIG, [put_capture(storage, cid, fx.trip_updates_feed())])
    assert len(storage.list_keys(MANIFESTS)) == 2
    result = compact_hour(storage, CONFIG, "manifests", DT, HOUR)
    assert result.rows == 2 and result.inputs_removed == 2
    assert storage.list_keys(MANIFESTS) == [MANIFESTS + COMPACTED]


def test_backfill_builds_both_tables_from_raw_and_is_idempotent(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    e1 = put_capture(storage, "c1", fx.trip_updates_feed())
    e2 = put_capture(storage, "c2", fx.trip_updates_feed())
    _raw_manifest(storage, "run1", [e1, e2])
    normalize_batch(storage, CONFIG, [e1], dim_routes={})  # raw quarantine object for c1 only

    first = backfill_manifests(storage, CONFIG)
    assert first.manifest_rows == 2 and first.quarantine_rows > 0
    assert first.hours_rewritten == 2  # the manifests hour and the quarantine hour
    assert storage.list_keys(MANIFESTS) == [MANIFESTS + COMPACTED]
    snapshot = {k: storage.get_bytes(k) for k in storage.list_keys("curated/")}

    second = backfill_manifests(storage, CONFIG)
    assert second.manifest_rows == first.manifest_rows
    assert second.hours_rewritten == 0
    assert {k: storage.get_bytes(k) for k in storage.list_keys("curated/")} == snapshot


def test_backfill_merges_with_live_parts_without_duplicates(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    e1 = put_capture(storage, "c1", fx.trip_updates_feed())
    e2 = put_capture(storage, "c2", fx.trip_updates_feed())
    _raw_manifest(storage, "run1", [e1, e2])
    normalize_batch(storage, CONFIG, [e1, e2], dim_routes=ROUTES)
    write_manifest_parts(storage, CONFIG, [e1])  # live curation already wrote c1

    result = backfill_manifests(storage, CONFIG)
    assert result.manifest_rows == 2
    assert sorted(str(r["capture_id"]) for r in _rows(storage, MANIFESTS)) == ["c1", "c2"]
    assert storage.list_keys(MANIFESTS) == [MANIFESTS + COMPACTED]
