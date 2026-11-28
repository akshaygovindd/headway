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
from headway.curate.meta import backfill_manifests, manifest_row, write_manifest_parts
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


def _duplicate_record(storage: LocalStorage, run_id: str, entries: list[object]) -> None:
    doc = {
        "run_id": run_id,
        "duplicate_of": "manifests/boston/x.json",
        "entries": [e.model_dump(mode="json") for e in entries],  # type: ignore[attr-defined]
    }
    storage.put_bytes(
        f"manifests/boston/duplicates/dt={DT}/hour={HOUR}/{run_id}.json", json.dumps(doc).encode()
    )


def test_duplicate_delivery_records_are_flagged_in_manifests_but_never_curated_as_data(
    tmp_path: Path,
) -> None:
    from headway.curate.pipeline import curate_new

    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "c1", fx.trip_updates_feed())
    twin = put_capture(storage, "c1-twin", fx.trip_updates_feed())
    _raw_manifest(storage, "r1", [entry])
    _duplicate_record(storage, "r2", [twin])

    result = curate_new(storage, CONFIG, dim_routes=ROUTES)

    assert result.rows() == {"trip_updates": 2}  # the original capture only
    rows = {r["capture_id"]: r["status"] for r in _rows(storage, MANIFESTS)}
    assert rows == {"c1": "ok", "c1-twin": "duplicate_delivery"}
    curate_new(storage, CONFIG, dim_routes=ROUTES)  # idempotent
    assert len(_rows(storage, MANIFESTS)) == 2


def test_backfill_includes_duplicate_delivery_records_with_their_flag(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    entry = put_capture(storage, "c1", fx.trip_updates_feed())
    twin = entry.model_copy(update={"run_id": "run2"})  # a delivery has its own run id
    _raw_manifest(storage, "r1", [entry])
    _duplicate_record(storage, "r2", [twin])
    backfill_manifests(storage, CONFIG)
    statuses = sorted(str(r["status"]) for r in _rows(storage, MANIFESTS))
    assert statuses == ["duplicate_delivery", "ok"]


def test_polls_that_share_a_capture_id_are_both_kept(tmp_path: Path) -> None:
    """Vehicle polls 0 and 1 are fetched a fraction of a second apart; with the same payload they
    share a capture id (feed, fetch second, payload hash). Poll 1 used to replace poll 0, so the
    minute had no poll 0 and read as missing in pipeline health and the capture SLO."""
    storage = LocalStorage(tmp_path)
    poll0 = put_capture(storage, "vp-1", fx.trip_updates_feed(), feed="vehicle_positions")
    poll1 = poll0.model_copy(update={"poll_index": 1})
    write_manifest_parts(storage, CONFIG, [poll0, poll1])
    rows = _rows(storage, MANIFESTS)
    assert sorted((str(r["capture_id"]), r["poll_index"]) for r in rows) == [
        ("vp-1", 0),
        ("vp-1", 1),
    ]


def test_backfill_restores_a_poll_zero_row_the_live_curation_lost(tmp_path: Path) -> None:
    """Production state before the fix: the curated hour holds only poll 1 of a shared capture
    id (and rows from before 15 s sampling with a null poll_index). Raw is the source of truth:
    the backfill adds poll 0, keeps poll 1 once, and a null poll_index is not a duplicate."""
    storage = LocalStorage(tmp_path)
    poll0 = put_capture(storage, "vp-1", fx.trip_updates_feed(), feed="vehicle_positions")
    poll1 = poll0.model_copy(update={"poll_index": 1})
    old = put_capture(storage, "tu-1", fx.trip_updates_feed())
    _raw_manifest(storage, "run1", [poll0, poll1, old])
    legacy = pa.table(  # what live curation wrote: poll 1 only, and a null poll_index row
        {
            name: [
                {**manifest_row(poll1)}[name],
                {**manifest_row(old), "poll_index": None}[name],
            ]
            for name in META_SCHEMAS["manifests"].names
        },
        schema=META_SCHEMAS["manifests"],
    )
    buf = io.BytesIO()
    pq.write_table(legacy, buf)
    storage.put_bytes(MANIFESTS + "part-legacy.parquet", buf.getvalue())

    result = backfill_manifests(storage, CONFIG)

    assert result.manifest_rows == 3
    assert sorted((str(r["capture_id"]), r["poll_index"]) for r in _rows(storage, MANIFESTS)) == [
        ("tu-1", 0),
        ("vp-1", 0),
        ("vp-1", 1),
    ]
