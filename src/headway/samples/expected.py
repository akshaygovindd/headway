"""Parse and validate every capture in a sample; summarise counts for EXPECTED.json."""

import gzip
import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from headway.config import CityConfig
from headway.contracts.manifest import ManifestEntry
from headway.contracts.quarantine import QuarantineReason, validate_records
from headway.contracts.records import AlertRecord, TripUpdateRecord, VehiclePositionRecord
from headway.parse.gtfs_rt import (
    CaptureMeta,
    ParseError,
    Row,
    parse_alerts,
    parse_trip_updates,
    parse_vehicle_positions,
)
from headway.parse.gtfs_static import GtfsSource, Route

PARSERS: dict[str, tuple[Callable[[bytes, CaptureMeta], list[Row]], type[BaseModel]]] = {
    "trip_updates": (parse_trip_updates, TripUpdateRecord),
    "vehicle_positions": (parse_vehicle_positions, VehiclePositionRecord),
    "alerts": (parse_alerts, AlertRecord),
}


def iter_entries(sample: Path, prefix: str) -> list[ManifestEntry]:
    entries: list[ManifestEntry] = []
    for path in sorted((sample / "manifests" / prefix).rglob("*.json")):
        doc = json.loads(path.read_text())
        entries += [ManifestEntry.model_validate(e) for e in doc["entries"]]
    return entries


def _dim_routes(sample: Path) -> dict[str, int] | None:
    gtfs = sample / "gtfs"
    if not gtfs.is_dir():
        return None
    return {r.route_id: r.route_type for r in GtfsSource(gtfs).iter_rows(Route)}


def compute_expected(sample: Path, config: CityConfig) -> dict[str, Any]:
    entries = iter_entries(sample, config.storage_prefix)
    captures: Counter[str] = Counter()
    failed_fetches: Counter[str] = Counter()
    parsed: Counter[str] = Counter()
    valid: Counter[str] = Counter()
    quarantine: dict[str, Counter[str]] = {f: Counter() for f in PARSERS}
    unparseable: Counter[str] = Counter()
    header_failures = 0
    dim_routes = _dim_routes(sample)
    curated_rows: Counter[str] = Counter()
    filtered: Counter[str] = Counter()
    curated_quarantine: dict[str, Counter[str]] = {f: Counter() for f in PARSERS}

    for entry in entries:
        if entry.status != "ok" or not entry.s3_key:
            failed_fetches[entry.feed] += 1
            continue
        captures[entry.feed] += 1
        if entry.header_parse_error:
            header_failures += 1
        parse, model = PARSERS[entry.feed]
        data = gzip.decompress((sample / entry.s3_key).read_bytes())
        meta = CaptureMeta(entry.capture_id, entry.fetched_at)
        try:
            rows = parse(data, meta)
        except ParseError:
            unparseable[entry.feed] += 1
            quarantine[entry.feed][QuarantineReason.PROTOBUF_PARSE_ERROR.value] += 1
            curated_quarantine[entry.feed][QuarantineReason.PROTOBUF_PARSE_ERROR.value] += 1
            continue
        good, bad = validate_records(rows, model, {"bbox": config.bbox})
        parsed[entry.feed] += len(rows)
        valid[entry.feed] += len(good)
        for q in bad:
            quarantine[entry.feed][q.reason.value] += 1
            curated_quarantine[entry.feed][q.reason.value] += 1
        for rec in good:
            route_id = getattr(rec, "route_id", None)
            if dim_routes is None or route_id is None:
                curated_rows[entry.feed] += 1
            elif route_id not in dim_routes:
                curated_quarantine[entry.feed][QuarantineReason.UNKNOWN_ROUTE.value] += 1
            elif dim_routes[route_id] in config.route_types:
                curated_rows[entry.feed] += 1
            else:
                filtered[entry.feed] += 1

    expected: dict[str, Any] = {
        "captures_per_feed": dict(sorted(captures.items())),
        "failed_fetches_per_feed": dict(sorted(failed_fetches.items())),
        "header_parse_failures": header_failures,
        "unparseable_captures_per_feed": dict(sorted(unparseable.items())),
        "rows_parsed": dict(sorted(parsed.items())),
        "rows_valid": dict(sorted(valid.items())),
        "quarantine_by_reason": {f: dict(sorted(c.items())) for f, c in sorted(quarantine.items())},
    }
    if dim_routes is not None:
        # What the curated layer must hold: valid rows in scope (or without route_id); rows of
        # routes outside route_types are filtered, routes absent from GTFS are quarantined.
        # Duplicate captures are never dropped.
        expected["curated"] = {
            "filtered_out_of_scope": dict(sorted(filtered.items())),
            "rows_per_table": dict(sorted(curated_rows.items())),
            "quarantine_by_reason": {
                f: dict(sorted(c.items())) for f, c in sorted(curated_quarantine.items())
            },
        }
    return expected


def render_expected(expected: dict[str, Any]) -> str:
    return json.dumps(expected, indent=2, sort_keys=True) + "\n"
