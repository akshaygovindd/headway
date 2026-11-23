import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_curate_normalize import put_capture

from fixtures import gtfs_rt as fx
from fixtures.rows import FETCHED_AT
from headway.config import load_city_config
from headway.contracts.manifest import ManifestEntry
from headway.curate.pipeline import curate_new, read_watermark
from headway.storage import LocalStorage

CONFIG = load_city_config("boston")
ROUTES = {"Red": 1}


def write_manifest(storage: LocalStorage, run_id: str, entries: list[ManifestEntry]) -> None:
    ts = entries[0].fetched_at
    key = f"manifests/boston/dt={ts:%Y-%m-%d}/hour={ts:%H}/{run_id}.json"
    doc = {"run_id": run_id, "entries": [e.model_dump(mode="json") for e in entries]}
    storage.put_bytes(key, json.dumps(doc).encode())


class FlakyStorage(LocalStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.fail = True

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        if self.fail and key.startswith("curated/"):
            raise OSError("disk full")
        super().put_bytes(key, data, content_type, cache_control)


def seed(storage: LocalStorage) -> list[ManifestEntry]:
    e1 = put_capture(storage, "c1", fx.trip_updates_feed())
    e2 = put_capture(storage, "c2", fx.trip_updates_feed()).model_copy(
        update={"fetched_at": FETCHED_AT + timedelta(hours=1)}
    )
    write_manifest(storage, "r1", [e1])
    write_manifest(storage, "r2", [e2])
    return [e1, e2]


def test_watermark_not_advanced_when_write_fails(tmp_path: Path) -> None:
    storage = FlakyStorage(tmp_path)
    seed(storage)
    with pytest.raises(OSError):
        curate_new(storage, CONFIG, dim_routes=ROUTES)
    assert read_watermark(storage, "boston") is None

    storage.fail = False
    result = curate_new(storage, CONFIG, dim_routes=ROUTES)
    assert result.rows() == {"trip_updates": 4}
    mark = read_watermark(storage, "boston")
    assert mark is not None and mark.fetched_at == FETCHED_AT + timedelta(hours=1)


def test_second_run_processes_nothing_new(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    seed(storage)
    curate_new(storage, CONFIG, dim_routes=ROUTES)
    again = curate_new(storage, CONFIG, dim_routes=ROUTES)
    assert again.batches == []


def test_same_instant_new_capture_is_picked_up(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    seed(storage)
    curate_new(storage, CONFIG, dim_routes=ROUTES)
    late = put_capture(storage, "c3", fx.trip_updates_feed()).model_copy(
        update={"fetched_at": FETCHED_AT + timedelta(hours=1)}
    )
    write_manifest(storage, "r3", [late])
    result = curate_new(storage, CONFIG, dim_routes=ROUTES)
    assert result.rows() == {"trip_updates": 2}


def _vehicle_run(storage: LocalStorage, run_id: str, base: ManifestEntry) -> list[ManifestEntry]:
    """Four vehicle captures of one scheduled minute, as 15 s sampling writes them."""
    entries = []
    for poll in range(4):
        cid = f"{run_id}-vp{poll}"
        e = put_capture(
            storage, cid, fx.vehicles_feed(lat=42.35 + poll / 1000), "vehicle_positions"
        )
        entries.append(
            e.model_copy(
                update={
                    "run_id": run_id,
                    "poll_index": poll,
                    "scheduled_at": base.scheduled_at,
                    "fetched_at": base.fetched_at + timedelta(seconds=15 * poll),
                }
            )
        )
    return entries


def test_four_vehicle_captures_of_one_minute_are_curated_with_their_poll_index(
    tmp_path: Path,
) -> None:
    import io

    import pyarrow.parquet as pq

    storage = LocalStorage(tmp_path)
    base = put_capture(storage, "base", fx.trip_updates_feed())
    entries = _vehicle_run(storage, "r1", base)
    write_manifest(storage, "r1", entries)
    result = curate_new(storage, CONFIG, dim_routes=ROUTES)
    assert result.rows() == {"vehicle_positions": 4}  # one row per poll, none merged away

    manifest_rows = []
    for key in storage.list_keys("curated/boston/table=manifests/"):
        manifest_rows += pq.read_table(io.BytesIO(storage.get_bytes(key))).to_pylist()
    assert sorted(r["poll_index"] for r in manifest_rows) == [0, 1, 2, 3]
    assert len({r["scheduled_at"] for r in manifest_rows}) == 1  # still one capture minute
    mark = read_watermark(storage, "boston")
    assert mark is not None and mark.fetched_at == base.fetched_at + timedelta(seconds=45)
    # second pass: nothing new, nothing doubled
    assert curate_new(storage, CONFIG, dim_routes=ROUTES).rows() == {}


def test_manifest_without_poll_index_reads_as_poll_zero() -> None:
    entry = ManifestEntry.model_validate(
        {
            "run_id": "r",
            "capture_id": "c",
            "feed": "alerts",
            "city_id": "boston",
            "status": "error",
            "scheduled_at": "2026-06-01T12:00:00Z",
            "fetched_at": "2026-06-01T12:00:05Z",
            "error": "x",
        }
    )
    assert entry.poll_index == 0
