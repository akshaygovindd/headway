import zipfile
from datetime import date
from pathlib import Path

import pytest

from headway.parse.gtfs_static import (
    GtfsParseError,
    GtfsSource,
    Route,
    StopTime,
    write_table,
)

FILES = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone,extra\n"
    "1,MBTA,http://x,America/New_York,z\n",
    "routes.txt": "route_id,route_type,route_long_name,route_color\n"
    "Red,1,Red Line,\nBus1,3,,FFFFFF\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
    "place-a,A,42.1,-71.1,1,\ns1,A Platform,42.1,-71.1,0,place-a\n",
    "trips.txt": "route_id,service_id,trip_id,direction_id,shape_id\nRed,svc,t1,0,sh1\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
    "t1,25:10:00,25:10:30,s1,1\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nsh1,42.1,-71.1,1\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,"
    "start_date,end_date\nsvc,0,0,0,0,0,0,1,20261101,20261231\n",
    "calendar_dates.txt": "service_id,date,exception_type\nsvc,20261126,2\n",
    "feed_info.txt": "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date\n"
    "MBTA,http://x,en,20261101\n",
}


def _folder(tmp_path: Path, skip: tuple[str, ...] = ()) -> Path:
    folder = tmp_path / "gtfs"
    folder.mkdir()
    for name, text in FILES.items():
        if name not in skip:
            (folder / name).write_text(text, encoding="utf-8")
    return folder


def test_reads_all_tables_from_folder(tmp_path: Path) -> None:
    src = GtfsSource(_folder(tmp_path))
    assert next(src.agency()).agency_name == "MBTA"
    routes = list(src.routes())
    assert [r.route_type for r in routes] == [1, 3]
    assert routes[0].route_color is None and routes[0].route_long_name == "Red Line"
    stops = list(src.stops())
    assert stops[1].parent_station == "place-a" and stops[0].parent_station is None
    assert stops[0].stop_lat == 42.1
    assert next(src.trips()).direction_id == 0
    st = next(src.stop_times())
    assert st.arrival_time == "25:10:00" and st.stop_sequence == 1
    assert next(src.shapes()).shape_pt_sequence == 1
    cal = next(src.calendar())
    assert cal.sunday and not cal.monday
    assert cal.runs_on(date(2026, 11, 22)) and not cal.runs_on(date(2026, 11, 23))
    assert next(src.calendar_dates()).date == date(2026, 11, 26)
    assert next(src.feed_info()).feed_start_date == date(2026, 11, 1)


def test_reads_from_zip(tmp_path: Path) -> None:
    archive = tmp_path / "g.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, text in FILES.items():
            zf.writestr(name, text)
    src = GtfsSource(archive)
    assert [r.route_id for r in src.routes()] == ["Red", "Bus1"]


def test_optional_tables_may_be_absent(tmp_path: Path) -> None:
    src = GtfsSource(_folder(tmp_path, skip=("shapes.txt", "feed_info.txt", "calendar.txt")))
    assert list(src.shapes()) == []
    assert list(src.feed_info()) == []
    assert list(src.calendar()) == []


def test_required_table_missing_raises(tmp_path: Path) -> None:
    src = GtfsSource(_folder(tmp_path, skip=("trips.txt",)))
    with pytest.raises(GtfsParseError, match="trips"):
        list(src.trips())


def test_invalid_row_reports_line(tmp_path: Path) -> None:
    folder = _folder(tmp_path)
    (folder / "routes.txt").write_text("route_id,route_type\nRed,notanint\n", encoding="utf-8")
    with pytest.raises(GtfsParseError, match=r"routes.txt line 2"):
        list(GtfsSource(folder).routes())


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        GtfsSource(tmp_path / "nope")


def test_write_table_round_trips(tmp_path: Path) -> None:
    src = GtfsSource(_folder(tmp_path))
    out = tmp_path / "routes.txt"
    assert write_table(out, Route, src.routes()) == 2
    assert out.read_text().startswith("route_id,agency_id,")
    again = tmp_path / "again"
    again.mkdir()
    (again / "routes.txt").write_text(out.read_text())
    assert list(GtfsSource(again).routes()) == list(src.routes())
    out2 = tmp_path / "st.txt"
    write_table(out2, StopTime, src.stop_times())
    assert "25:10:00" in out2.read_text()
