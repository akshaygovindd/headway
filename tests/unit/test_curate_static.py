import time
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


def _write_gtfs(root: Path) -> Path:
    files = {
        "routes.txt": "route_id,route_type,route_long_name\nRed,1,Red Line\nBus1,3,Bus One\n",
        "trips.txt": "route_id,service_id,trip_id,shape_id\n"
        "Red,wk,t_red,shp_red\nBus1,wk,t_bus,shp_bus\n",
        "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "t_red,05:00:00,05:00:00,plat1,1\nt_red,05:05:00,05:05:00,plat2,2\n"
        "t_bus,06:00:00,06:00:00,busstop,1\n",
        "stops.txt": "stop_id,stop_name,location_type,parent_station,stop_lat,stop_lon\n"
        "plat1,P1,0,sta1,42.0,-71.0\nplat2,P2,0,sta2,42.1,-71.1\nsta1,S1,1,,42.0,-71.0\n"
        "sta2,S2,1,,42.1,-71.1\nbusstop,B,0,,42.2,-71.2\nother,O,0,,42.3,-71.3\n",
        "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n"
        "shp_red,42.0,-71.0,1\nshp_red,42.1,-71.1,2\nshp_bus,42.2,-71.2,1\n",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
        "start_date,end_date\nwk,1,1,1,1,1,0,0,20260101,20261231\n"
        "unused,0,0,0,0,0,1,1,20260101,20261231\n",
        "calendar_dates.txt": "service_id,date,exception_type\nunused,20260704,1\n",
        "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang,feed_version\n"
        "X,http://x,en,v1\n",
    }
    root.mkdir()
    for name, text in files.items():
        (root / name).write_text(text)
    return root


def test_load_static_drops_out_of_scope_rows_and_keeps_all_routes(tmp_path: Path) -> None:
    gtfs = _write_gtfs(tmp_path / "gtfs")
    storage = LocalStorage(tmp_path / "out")
    result = load_static(storage, CONFIG, GtfsSource(gtfs))

    def col(table: str, column: str) -> set[str]:
        key = static_key("boston", table, "v1")
        return set(pq.read_table(tmp_path / "out" / key).column(column).to_pylist())

    assert col("routes", "route_id") == {"Red", "Bus1"}
    assert col("trips", "trip_id") == {"t_red"}
    assert col("stop_times", "trip_id") == {"t_red"}
    assert col("shapes", "shape_id") == {"shp_red"}
    assert col("stops", "stop_id") == {"plat1", "plat2", "sta1", "sta2"}
    assert col("calendar", "service_id") == {"wk", "unused"}
    assert col("calendar_dates", "service_id") == {"unused"}
    assert result.rows["stop_times"] == 2 and result.rows["feed_info"] == 1


def test_load_static_on_sample_is_fast(tmp_path: Path) -> None:
    started = time.monotonic()
    load_static(LocalStorage(tmp_path), CONFIG, GtfsSource(GTFS))
    assert time.monotonic() - started < 5.0


def _rows(tmp_path: Path, table: str, version: str) -> list[dict[str, object]]:
    return pq.read_table(tmp_path / static_key("boston", table, version)).to_pylist()


def test_geometry_tables_are_written_with_distances_along_the_shape(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    result = load_static(storage, CONFIG, GtfsSource(_write_gtfs(tmp_path / "g")))
    assert result.rows["shape_points_dist"] == 2 and result.rows["stop_shape_positions"] == 2
    pts = _rows(tmp_path, "shape_points_dist", "v1")
    assert [p["shape_pt_sequence"] for p in pts] == [1, 2]
    assert pts[0]["dist_m"] == 0.0
    assert 13_000 < pts[1]["dist_m"] < 14_500  # 0.1 deg lat + 0.1 deg lon near 42 N
    assert {p["feed_version"] for p in pts} == {"v1"}


def test_stop_positions_follow_trip_order_and_project_onto_the_shape() -> None:
    import pyarrow as pa

    from headway.curate.geometry import build_geometry

    # a straight east-west shape; stops sit slightly north of it, in order
    shapes = pa.table(
        {
            "shape_id": ["s"] * 3,
            "shape_pt_sequence": [1, 2, 3],
            "shape_pt_lat": [42.0, 42.0, 42.0],
            "shape_pt_lon": [-71.00, -71.01, -71.02],
        }
    )
    stops = pa.table(
        {
            "stop_id": ["a", "b", "c"],
            "stop_lat": [42.0005, 42.0005, 42.0005],
            "stop_lon": [-71.0, -71.01, -71.02],
        }
    )
    trips = pa.table({"trip_id": ["t1", "t2"], "shape_id": ["s", "s"]})
    stop_times = pa.table(
        {
            "trip_id": ["t1", "t1", "t1", "t2", "t2"],
            "stop_sequence": [10, 20, 30, 10, 20],
            "stop_id": ["a", "b", "c", "a", "c"],
        }
    )
    pts, positions = build_geometry(shapes, stops, trips, stop_times)
    by = {(p["stop_id"], p["stop_sequence"]): p["dist_m"] for p in positions}
    assert len(pts) == 3 and pts[2]["dist_m"] > 1_600
    assert by[("a", 10)] == 0.0
    assert abs(by[("b", 20)] - pts[1]["dist_m"]) < 1.0
    assert abs(by[("c", 30)] - pts[2]["dist_m"]) < 1.0
    assert by[("c", 20)] == by[("c", 30)]  # the short-turn pattern gets its own sequence key


def test_stop_on_a_shape_that_doubles_back_stays_in_trip_order() -> None:
    import pyarrow as pa

    from headway.curate.geometry import build_geometry

    # out and back over the same line: the second visit of the stop must map to the way back
    shapes = pa.table(
        {
            "shape_id": ["s"] * 3,
            "shape_pt_sequence": [1, 2, 3],
            "shape_pt_lat": [42.0, 42.0, 42.0],
            "shape_pt_lon": [-71.00, -71.02, -71.00],
        }
    )
    stops = pa.table({"stop_id": ["x", "y"], "stop_lat": [42.0, 42.0], "stop_lon": [-71.0, -71.02]})
    trips = pa.table({"trip_id": ["t"], "shape_id": ["s"]})
    stop_times = pa.table(
        {"trip_id": ["t"] * 3, "stop_sequence": [1, 2, 3], "stop_id": ["x", "y", "x"]}
    )
    _, positions = build_geometry(shapes, stops, trips, stop_times)
    by = {p["stop_sequence"]: p["dist_m"] for p in positions}
    assert by[1] == 0.0 and by[1] < by[2] < by[3]


def test_a_version_loaded_before_geometry_existed_gets_it_on_the_next_run(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    source = GtfsSource(_write_gtfs(tmp_path / "g"))
    load_static(storage, CONFIG, source)
    for name in ("shape_points_dist", "stop_shape_positions"):
        storage.delete(static_key("boston", name, "v1"))
    again = load_static(storage, CONFIG, source)
    assert again.skipped and set(again.rows) == {"shape_points_dist", "stop_shape_positions"}
    assert load_static(storage, CONFIG, source).rows == {}
