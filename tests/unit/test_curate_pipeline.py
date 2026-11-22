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
