from datetime import UTC, timedelta

import pytest

from fixtures import gtfs_rt as fx
from fixtures.rows import BBOX, FETCHED_AT, HEADER_TS
from headway.contracts.quarantine import QuarantineReason, validate_records
from headway.contracts.records import AlertRecord, TripUpdateRecord, VehiclePositionRecord
from headway.parse.gtfs_rt import (
    CaptureMeta,
    ParseError,
    parse_alerts,
    parse_header,
    parse_trip_updates,
    parse_vehicle_positions,
)

META = CaptureMeta(capture_id="cap1", fetched_at=FETCHED_AT)
CTX = {"bbox": BBOX}


def test_header() -> None:
    ts, count, version = parse_header(fx.trip_updates_feed())
    assert ts == HEADER_TS and ts.tzinfo is UTC
    assert (count, version) == (1, "2.0")


@pytest.mark.parametrize("data", [fx.CORRUPT, b"", b"\x08"])
def test_corrupt_bytes_raise(data: bytes) -> None:
    for fn in (parse_header, lambda d: parse_trip_updates(d, META)):
        with pytest.raises(ParseError):
            fn(data)


def test_missing_header_timestamp() -> None:
    with pytest.raises(ParseError):
        parse_header(fx.trip_updates_feed(header_ts=None))


def test_trip_updates_one_row_per_stop_time_update() -> None:
    rows = parse_trip_updates(fx.trip_updates_feed(), META)
    assert len(rows) == 2
    r = rows[0]
    assert (r["trip_id"], r["route_id"], r["direction_id"], r["stop_sequence"]) == (
        "t1",
        "Red",
        0,
        3,
    )
    assert r["predicted_departure_ts"] is None and r["uncertainty"] == 30
    assert r["schedule_relationship"] == "SCHEDULED"
    assert r["capture_id"] == "cap1" and r["feed_header_ts"] == HEADER_TS
    valid, bad = validate_records(rows, TripUpdateRecord, CTX)
    assert len(valid) == 2 and not bad


def test_vehicles() -> None:
    rows = parse_vehicle_positions(fx.vehicles_feed(), META)
    assert len(rows) == 1 and rows[0]["current_status"] == "IN_TRANSIT_TO"
    valid, bad = validate_records(rows, VehiclePositionRecord, CTX)
    assert len(valid) == 1 and not bad


def test_alerts_one_row_per_informed_entity() -> None:
    rows = parse_alerts(fx.alerts_feed(), META)
    assert [r["entity_index"] for r in rows] == [0, 1]
    assert rows[1]["stop_id"] == "70061" and rows[0]["stop_id"] is None
    assert rows[0]["effect"] == "SIGNIFICANT_DELAYS" and rows[0]["header_text"]
    valid, bad = validate_records(rows, AlertRecord, CTX)
    assert len(valid) == 2 and not bad


def test_alert_without_informed_entities_keeps_one_row() -> None:
    assert len(parse_alerts(fx.alerts_feed(informed=0), META)) == 1


def _reason(data: bytes, parser, model) -> QuarantineReason:  # type: ignore[no-untyped-def]
    valid, bad = validate_records(parser(data, META), model, CTX)
    assert not valid
    return bad[0].reason


def test_fixture_per_quarantine_reason() -> None:
    assert (
        _reason(fx.trip_updates_feed(trip_id=""), parse_trip_updates, TripUpdateRecord)
        is QuarantineReason.MISSING_TRIP_ID
    )
    assert (
        _reason(fx.trip_updates_feed(stop_id=""), parse_trip_updates, TripUpdateRecord)
        is QuarantineReason.MISSING_STOP_ID
    )
    assert (
        _reason(fx.vehicles_feed(lat=10.0), parse_vehicle_positions, VehiclePositionRecord)
        is QuarantineReason.INVALID_COORDINATES
    )
    assert (
        _reason(
            fx.trip_updates_feed(arrival=FETCHED_AT + timedelta(hours=48)),
            parse_trip_updates,
            TripUpdateRecord,
        )
        is QuarantineReason.TIMESTAMP_OUT_OF_RANGE
    )
    assert (
        _reason(fx.trip_updates_feed(arrival=None), parse_trip_updates, TripUpdateRecord)
        is QuarantineReason.SCHEMA_ERROR
    )
