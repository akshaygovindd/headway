from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from fixtures.rows import (
    BBOX,
    FETCHED_AT,
    HEADER_TS,
    alert_row,
    trip_update_row,
    vehicle_row,
)
from headway.contracts.manifest import ManifestEntry
from headway.contracts.quarantine import QuarantineReason, validate_records
from headway.contracts.records import AlertRecord, TripUpdateRecord, VehiclePositionRecord

CTX = {"bbox": BBOX}


def test_valid_rows() -> None:
    t, tq = validate_records([trip_update_row()], TripUpdateRecord, CTX)
    v, vq = validate_records([vehicle_row()], VehiclePositionRecord, CTX)
    a, aq = validate_records([alert_row()], AlertRecord, CTX)
    assert len(t) == len(v) == len(a) == 1
    assert not (tq or vq or aq)


CASES: list[tuple[type[Any], dict[str, Any], QuarantineReason]] = [
    (TripUpdateRecord, trip_update_row(trip_id=""), QuarantineReason.MISSING_TRIP_ID),
    (TripUpdateRecord, trip_update_row(trip_id=None), QuarantineReason.MISSING_TRIP_ID),
    (TripUpdateRecord, trip_update_row(stop_id=""), QuarantineReason.MISSING_STOP_ID),
    (TripUpdateRecord, trip_update_row(stop_id=None), QuarantineReason.MISSING_STOP_ID),
    (
        TripUpdateRecord,
        trip_update_row(predicted_arrival_ts=FETCHED_AT - timedelta(hours=7)),
        QuarantineReason.TIMESTAMP_OUT_OF_RANGE,
    ),
    (
        TripUpdateRecord,
        trip_update_row(predicted_arrival_ts=FETCHED_AT + timedelta(hours=25)),
        QuarantineReason.TIMESTAMP_OUT_OF_RANGE,
    ),
    (
        TripUpdateRecord,
        trip_update_row(predicted_arrival_ts=None),
        QuarantineReason.SCHEMA_ERROR,
    ),
    (TripUpdateRecord, trip_update_row(stop_sequence=-1), QuarantineReason.SCHEMA_ERROR),
    (TripUpdateRecord, trip_update_row(bogus=1), QuarantineReason.SCHEMA_ERROR),
    (
        TripUpdateRecord,
        trip_update_row(predicted_arrival_ts=datetime(2026, 6, 1, 12, 5)),
        QuarantineReason.SCHEMA_ERROR,
    ),
    (VehiclePositionRecord, vehicle_row(lat=10.0), QuarantineReason.INVALID_COORDINATES),
    (VehiclePositionRecord, vehicle_row(lon=999.0), QuarantineReason.INVALID_COORDINATES),
    (VehiclePositionRecord, vehicle_row(vehicle_id=None), QuarantineReason.SCHEMA_ERROR),
    (AlertRecord, alert_row(entity_index=-1), QuarantineReason.SCHEMA_ERROR),
]


@pytest.mark.parametrize(("model", "row", "reason"), CASES)
def test_quarantine_reason(model: type[Any], row: dict[str, Any], reason: QuarantineReason) -> None:
    valid, bad = validate_records([row], model, CTX)
    assert valid == []
    assert len(bad) == 1
    assert bad[0].reason is reason
    assert bad[0].error


def test_mixed_batch_splits() -> None:
    valid, bad = validate_records(
        [trip_update_row(), trip_update_row(trip_id=""), trip_update_row(trip_id="t2")],
        TripUpdateRecord,
        CTX,
    )
    assert [r.trip_id for r in valid] == ["t1", "t2"]
    assert len(bad) == 1


def test_vehicle_boundary_event_lag_ok() -> None:
    valid, _ = validate_records(
        [vehicle_row(event_ts=HEADER_TS - timedelta(minutes=10))], VehiclePositionRecord, CTX
    )
    assert len(valid) == 1
    assert valid[0].is_stale_position is False


def test_vehicle_old_event_ts_is_kept_and_flagged_stale() -> None:
    valid, bad = validate_records(
        [vehicle_row(event_ts=HEADER_TS - timedelta(minutes=11))], VehiclePositionRecord, CTX
    )
    assert bad == []
    assert len(valid) == 1
    assert valid[0].is_stale_position is True


def test_vehicle_without_event_ts_is_not_stale() -> None:
    valid, _ = validate_records([vehicle_row(event_ts=None)], VehiclePositionRecord, CTX)
    assert valid[0].is_stale_position is False


def test_vehicle_requires_bbox_context() -> None:
    with pytest.raises(ValueError):
        validate_records([vehicle_row()], VehiclePositionRecord, {})


def test_timestamps_normalized_to_utc() -> None:
    est = timezone(timedelta(hours=-5))
    local = FETCHED_AT.astimezone(est) + timedelta(minutes=1)
    (rec,), _ = validate_records(
        [trip_update_row(predicted_arrival_ts=local)], TripUpdateRecord, CTX
    )
    assert rec.predicted_arrival_ts is not None
    assert rec.predicted_arrival_ts.tzinfo is UTC


def _entry(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "r",
        "capture_id": "c",
        "feed": "alerts",
        "city_id": "boston",
        "status": "ok",
        "scheduled_at": FETCHED_AT,
        "fetched_at": FETCHED_AT,
        "s3_key": "raw/x",
        "content_hash": "ab",
    }
    base.update(over)
    return base


def test_manifest_ok_requires_key_and_hash() -> None:
    ManifestEntry.model_validate(_entry())
    for missing in ("s3_key", "content_hash"):
        with pytest.raises(ValidationError):
            ManifestEntry.model_validate(_entry(**{missing: None}))


def test_manifest_error_entry_and_forbid() -> None:
    e = ManifestEntry.model_validate(
        _entry(status="error", s3_key=None, content_hash=None, error="HTTP 500")
    )
    assert e.status == "error"
    with pytest.raises(ValidationError):
        ManifestEntry.model_validate(_entry(extra=1))


@pytest.mark.parametrize("relationship", ["SKIPPED", "NO_DATA"])
def test_trip_update_without_times_is_valid_when_skipped_or_no_data(relationship: str) -> None:
    row = trip_update_row(
        predicted_arrival_ts=None,
        predicted_departure_ts=None,
        schedule_relationship=relationship,
        event_ts=None,
        vehicle_id=None,
    )
    valid, bad = validate_records([row], TripUpdateRecord, CTX)
    assert len(valid) == 1 and not bad


def test_scheduled_trip_update_without_times_is_still_quarantined() -> None:
    row = trip_update_row(predicted_arrival_ts=None, predicted_departure_ts=None)
    valid, bad = validate_records([row], TripUpdateRecord, CTX)
    assert not valid and bad[0].reason == QuarantineReason.SCHEMA_ERROR
