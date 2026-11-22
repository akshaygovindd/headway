from pathlib import Path

import pyarrow.parquet as pq

from headway.config import load_city_config
from headway.curate.static import load_dim_routes, load_static, static_key
from headway.parse.gtfs_static import GtfsSource
from headway.storage import LocalStorage

CONFIG = load_city_config("boston")
GTFS = Path(__file__).resolve().parents[2] / "samples" / "boston" / "gtfs"


def test_load_static_writes_tables_then_skips_existing_version(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    first = load_static(storage, CONFIG, GtfsSource(GTFS))
    assert not first.skipped
    assert first.rows["routes"] > 0
    key = static_key("boston", "routes", first.feed_version)
    assert pq.read_table(tmp_path / key).num_rows == first.rows["routes"]
    before = storage.get_bytes(key)

    second = load_static(storage, CONFIG, GtfsSource(GTFS))
    assert second.skipped and second.rows == {}
    assert storage.get_bytes(key) == before


def test_dim_routes_maps_every_route_to_its_type(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    load_static(storage, CONFIG, GtfsSource(GTFS))
    routes = load_dim_routes(storage, CONFIG)
    assert routes["Red"] == 1
