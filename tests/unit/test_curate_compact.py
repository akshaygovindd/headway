from pathlib import Path

import pyarrow.parquet as pq
import pytest
from test_curate_normalize import put_capture

from fixtures import gtfs_rt as fx
from fixtures.rows import FETCHED_AT
from headway.config import load_city_config
from headway.curate import compact
from headway.curate.compact import COMPACTED, CompactionError, compact_all, compact_hour
from headway.curate.normalize import normalize_batch
from headway.storage import LocalStorage

CONFIG = load_city_config("boston")
ROUTES = {"Red": 1}
DT, HOUR = f"{FETCHED_AT:%Y-%m-%d}", f"{FETCHED_AT:%H}"
BASE = f"curated/boston/table=trip_updates/dt={DT}/hour={HOUR}/"


def two_batches(storage: LocalStorage) -> None:
    for cid in ("c1", "c2"):
        entry = put_capture(storage, cid, fx.trip_updates_feed())
        normalize_batch(storage, CONFIG, [entry], dim_routes=ROUTES)


def test_compaction_merges_parts_and_is_idempotent(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    two_batches(storage)
    assert len(storage.list_keys(BASE)) == 2

    first = compact_hour(storage, CONFIG, "trip_updates", DT, HOUR)
    assert first.compacted and first.rows == 4 and first.inputs_removed == 2
    assert storage.list_keys(BASE) == [BASE + COMPACTED]
    assert pq.read_table(tmp_path / BASE / COMPACTED).num_rows == 4
    before = storage.get_bytes(BASE + COMPACTED)

    second = compact_hour(storage, CONFIG, "trip_updates", DT, HOUR)
    assert not second.compacted
    assert storage.get_bytes(BASE + COMPACTED) == before


def test_later_parts_merge_into_existing_compacted(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    two_batches(storage)
    compact_hour(storage, CONFIG, "trip_updates", DT, HOUR)
    entry = put_capture(storage, "c3", fx.trip_updates_feed())
    normalize_batch(storage, CONFIG, [entry], dim_routes=ROUTES)
    result = compact_hour(storage, CONFIG, "trip_updates", DT, HOUR)
    assert result.rows == 6
    assert storage.list_keys(BASE) == [BASE + COMPACTED]


def test_row_count_mismatch_keeps_inputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    storage = LocalStorage(tmp_path)
    two_batches(storage)
    before = storage.list_keys(BASE)
    monkeypatch.setattr(compact.pq, "read_metadata", lambda _f: type("M", (), {"num_rows": 1})())
    with pytest.raises(CompactionError):
        compact_hour(storage, CONFIG, "trip_updates", DT, HOUR)
    assert storage.list_keys(BASE) == before


def test_compact_all_covers_every_hour(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    two_batches(storage)
    results = compact_all(storage, CONFIG)
    assert [r.rows for r in results] == [4]
    assert all(not r.compacted for r in compact_all(storage, CONFIG))
