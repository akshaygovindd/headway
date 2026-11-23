"""Shape geometry precomputed at static load: cumulative distance along each shape, and each
stop's position on it. Plain math only (no DuckDB spatial), so the dbt arrival reconstruction can
work with a single distance number per shape point and per stop.

Distances are metres along the polyline, haversine between consecutive shape points. The feed's
own ``shape_dist_traveled`` is ignored on purpose: it is optional and in unknown units.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

import pyarrow as pa

from headway.curate.schemas import STATIC_SCHEMAS

EARTH_M = 6_371_000.0
# A stop's projection may sit this far behind the previous stop's (shape wiggle, shared platforms).
BACKSTEP_M = 50.0
GEOMETRY_TABLES = ("shape_points_dist", "stop_shape_positions")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.asin(math.sqrt(h))


@dataclass(frozen=True)
class ShapePoint:
    seq: int
    lat: float
    lon: float
    dist_m: float


def cumulative(points: list[tuple[int, float, float]]) -> list[ShapePoint]:
    """Order (sequence, lat, lon) points and add cumulative distance from the first."""
    ordered = sorted(points)
    out: list[ShapePoint] = []
    total = 0.0
    for i, (seq, lat, lon) in enumerate(ordered):
        if i:
            total += haversine_m(ordered[i - 1][1], ordered[i - 1][2], lat, lon)
        out.append(ShapePoint(seq, lat, lon, total))
    return out


def project(shape: list[ShapePoint], lat: float, lon: float, min_dist_m: float = 0.0) -> float:
    """Distance along ``shape`` of the point on it nearest to (lat, lon).

    Only segments ending at or after ``min_dist_m`` are considered, which keeps consecutive
    stops of a trip in order on shapes that double back. Local equirectangular maths per segment.
    """
    if len(shape) == 1:
        return 0.0
    kx = math.cos(math.radians(lat)) * math.pi / 180
    best_off = math.inf
    best_dist = max(shape[-1].dist_m, min_dist_m)
    for a, b in zip(shape, shape[1:], strict=False):
        if b.dist_m < min_dist_m:
            continue
        ax, ay = (a.lon - lon) * kx, a.lat - lat
        bx, by = (b.lon - lon) * kx, b.lat - lat
        dx, dy = bx - ax, by - ay
        len2 = dx * dx + dy * dy
        t = 0.0 if len2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / len2))
        off = math.hypot(ax + t * dx, ay + t * dy)
        along = a.dist_m + t * (b.dist_m - a.dist_m)
        if off < best_off and along >= min_dist_m:
            best_off = off
            best_dist = along
    return best_dist


def build_geometry(
    shapes: pa.Table, stops: pa.Table, trips: pa.Table, stop_times: pa.Table
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Return (shape_points_dist rows, stop_shape_positions rows) without feed_version."""
    by_shape: dict[str, list[tuple[int, float, float]]] = defaultdict(list)
    for sid, seq, lat, lon in zip(
        shapes.column("shape_id").to_pylist(),
        shapes.column("shape_pt_sequence").to_pylist(),
        shapes.column("shape_pt_lat").to_pylist(),
        shapes.column("shape_pt_lon").to_pylist(),
        strict=True,
    ):
        by_shape[sid].append((seq, lat, lon))
    geometry = {sid: cumulative(pts) for sid, pts in by_shape.items()}

    coords = {
        sid: (lat, lon)
        for sid, lat, lon in zip(
            stops.column("stop_id").to_pylist(),
            stops.column("stop_lat").to_pylist(),
            stops.column("stop_lon").to_pylist(),
            strict=True,
        )
        if lat is not None and lon is not None
    }
    shape_of = dict(
        zip(trips.column("trip_id").to_pylist(), trips.column("shape_id").to_pylist(), strict=True)
    )
    patterns: dict[str, dict[tuple[tuple[int, str], ...], None]] = defaultdict(dict)
    per_trip: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for tid, seq, stop_id in zip(
        stop_times.column("trip_id").to_pylist(),
        stop_times.column("stop_sequence").to_pylist(),
        stop_times.column("stop_id").to_pylist(),
        strict=True,
    ):
        if shape_of.get(tid) in geometry:
            per_trip[tid].append((seq, stop_id))
    for tid, seq_stops in per_trip.items():
        patterns[shape_of[tid]][tuple(sorted(seq_stops))] = None  # one projection per pattern

    positions: dict[tuple[str, str, int], float] = {}
    for sid in sorted(patterns):
        for pattern in patterns[sid]:
            floor = 0.0
            for seq, stop_id in pattern:
                if stop_id not in coords:
                    continue
                lat, lon = coords[stop_id]
                d = project(geometry[sid], lat, lon, max(0.0, floor - BACKSTEP_M))
                floor = max(floor, d)
                positions.setdefault((sid, stop_id, seq), d)

    shape_rows: list[dict[str, object]] = [
        {
            "shape_id": sid,
            "shape_pt_sequence": p.seq,
            "lat": p.lat,
            "lon": p.lon,
            "dist_m": p.dist_m,
        }
        for sid in sorted(geometry)
        for p in geometry[sid]
    ]
    position_rows: list[dict[str, object]] = [
        {"shape_id": sid, "stop_id": stop_id, "stop_sequence": seq, "dist_m": d}
        for (sid, stop_id, seq), d in sorted(positions.items())
    ]
    return shape_rows, position_rows


def geometry_tables(
    shapes: pa.Table, stops: pa.Table, trips: pa.Table, stop_times: pa.Table, version: str
) -> dict[str, pa.Table]:
    """The two geometry tables typed by their curated schemas, stamped with feed_version."""
    shape_rows, position_rows = build_geometry(shapes, stops, trips, stop_times)
    out: dict[str, pa.Table] = {}
    for name, rows in (("shape_points_dist", shape_rows), ("stop_shape_positions", position_rows)):
        schema = STATIC_SCHEMAS[name]
        cols = {
            f.name: [r.get(f.name) if f.name != "feed_version" else version for r in rows]
            for f in schema
        }
        out[name] = pa.table(cols, schema=schema)
    return out
