"""Subset of a GTFS static feed covering the sample's routes and service dates."""

from collections.abc import Iterable
from datetime import date
from pathlib import Path

from headway.parse.gtfs_static import (
    Agency,
    Calendar,
    CalendarDate,
    FeedInfo,
    GtfsSource,
    Route,
    ShapePoint,
    Stop,
    StopTime,
    Trip,
    write_table,
)


def active_services(src: GtfsSource, days: Iterable[date]) -> set[str]:
    """Service ids running on any of ``days`` (calendar plus exceptions)."""
    wanted = set(days)
    active: set[str] = set()
    for cal in src.calendar():
        if any(cal.runs_on(d) for d in wanted):
            active.add(cal.service_id)
    for exc in src.calendar_dates():
        if exc.date in wanted:
            if exc.exception_type == 1:
                active.add(exc.service_id)
            elif exc.exception_type == 2:
                active.discard(exc.service_id)
    return active


def build_gtfs_subset(
    src: GtfsSource,
    out_dir: Path,
    route_ids: frozenset[str],
    route_types: frozenset[int],
    days: Iterable[date],
) -> dict[str, int]:
    """Write the subset tables to ``out_dir``; return row counts per table."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    # routes.txt keeps every route, as production does; the other tables cover subway only.
    routes = sorted(src.routes(), key=lambda r: r.route_id)
    kept_routes = {
        r.route_id for r in routes if r.route_id in route_ids and r.route_type in route_types
    }
    services = active_services(src, days)
    trips = sorted(
        (t for t in src.trips() if t.route_id in kept_routes and t.service_id in services),
        key=lambda t: t.trip_id,
    )
    trip_ids = {t.trip_id for t in trips}
    stop_times = sorted(
        src.iter_rows(StopTime, lambda raw: raw["trip_id"] in trip_ids),
        key=lambda s: (s.trip_id, s.stop_sequence),
    )

    all_stops = {s.stop_id: s for s in src.stops()}
    needed: set[str] = set()
    frontier = {st.stop_id for st in stop_times}
    while frontier:
        needed |= frontier
        frontier = {
            parent
            for sid in frontier
            if sid in all_stops and (parent := all_stops[sid].parent_station)
            if parent not in needed
        }
    stops = sorted((all_stops[s] for s in needed if s in all_stops), key=lambda s: s.stop_id)

    shape_ids = {t.shape_id for t in trips if t.shape_id}
    shapes = sorted(
        src.iter_rows(ShapePoint, lambda raw: raw["shape_id"] in shape_ids),
        key=lambda p: (p.shape_id, p.shape_pt_sequence),
    )
    calendar = sorted(
        (c for c in src.calendar() if c.service_id in {t.service_id for t in trips}),
        key=lambda c: c.service_id,
    )
    kept_services = {c.service_id for c in calendar} | {t.service_id for t in trips}
    calendar_dates = sorted(
        (c for c in src.calendar_dates() if c.service_id in kept_services),
        key=lambda c: (c.service_id, c.date),
    )
    agency = sorted(src.agency(), key=lambda a: a.agency_id or "")
    feed_info = list(src.feed_info())

    tables: list[tuple[str, type, list]] = [  # type: ignore[type-arg]
        ("agency", Agency, agency),
        ("routes", Route, routes),
        ("stops", Stop, stops),
        ("trips", Trip, trips),
        ("stop_times", StopTime, stop_times),
        ("shapes", ShapePoint, shapes),
        ("calendar", Calendar, calendar),
        ("calendar_dates", CalendarDate, calendar_dates),
        ("feed_info", FeedInfo, feed_info),
    ]
    for name, model, rows in tables:
        counts[name] = write_table(out_dir / f"{name}.txt", model, rows)
    return counts
