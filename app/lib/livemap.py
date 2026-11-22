"""Frames for the Live map page."""

from typing import Any

import pandas as pd

from lib.freshness import format_age
from lib.lines import hex_to_rgb, line_color

VEHICLE_COLUMNS = [
    "vehicle_id",
    "line",
    "trip",
    "next_stop",
    "position_age",
    "is_stale",
    "lon",
    "lat",
    "color",
]


def vehicles_frame(snapshot: dict[str, Any], dim_stop: pd.DataFrame) -> pd.DataFrame:
    """One row per vehicle with display fields; stale positions are flagged."""
    names = dict(zip(dim_stop["stop_id"], dim_stop["stop_name"], strict=True))
    rows = []
    for v in snapshot.get("vehicles", []):
        age = v.get("position_age_s")
        stop_id = v.get("stop_id")
        rows.append(
            {
                "vehicle_id": v["vehicle_id"],
                "line": v["route_id"],
                "trip": v.get("trip_id") or "unknown",
                "next_stop": names.get(stop_id, stop_id) if stop_id else "unknown",
                "position_age": format_age(age).replace(" ago", "")
                if age is not None
                else "unknown",
                "is_stale": bool(v.get("is_stale_position")),
                "lon": v["lon"],
                "lat": v["lat"],
                "color": hex_to_rgb(line_color(v["route_id"])),
            }
        )
    return pd.DataFrame(rows, columns=VEHICLE_COLUMNS)


def route_paths(shapes: pd.DataFrame) -> pd.DataFrame:
    """One row per (route, shape) with a [lon, lat] path and the official line colour."""
    rows = []
    ordered = shapes.sort_values(["route_id", "shape_id", "shape_pt_sequence"])
    for (route_id, shape_id), g in ordered.groupby(["route_id", "shape_id"]):
        rows.append(
            {
                "route_id": str(route_id),
                "shape_id": str(shape_id),
                "path": g[["shape_pt_lon", "shape_pt_lat"]].to_numpy().tolist(),
                "color": hex_to_rgb(line_color(str(route_id))),
            }
        )
    return pd.DataFrame(rows, columns=["route_id", "shape_id", "path", "color"])
