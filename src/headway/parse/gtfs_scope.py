"""Scoping rules shared by the static loader and the samples builder."""

from collections.abc import Iterable, Mapping


def route_ids_of_types(routes: Iterable[tuple[str, int]], route_types: Iterable[int]) -> set[str]:
    """Ids of the (route_id, route_type) pairs whose type is in ``route_types``."""
    wanted = set(route_types)
    return {route_id for route_id, route_type in routes if route_type in wanted}


def with_parent_stations(stop_ids: Iterable[str], parent_of: Mapping[str, str | None]) -> set[str]:
    """``stop_ids`` plus every ancestor station reachable through ``parent_of``."""
    needed: set[str] = set()
    frontier = set(stop_ids)
    while frontier:
        needed |= frontier
        frontier = {
            parent for sid in frontier if (parent := parent_of.get(sid)) and parent not in needed
        }
    return needed
