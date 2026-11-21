"""Record validation with quarantine of rejected rows."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ValidationError

from headway.contracts.records import (
    ERR_COORDINATES,
    ERR_TIMESTAMP,
    VehiclePositionRecord,
)


class QuarantineReason(StrEnum):
    SCHEMA_ERROR = "schema_error"
    MISSING_TRIP_ID = "missing_trip_id"
    MISSING_STOP_ID = "missing_stop_id"
    INVALID_COORDINATES = "invalid_coordinates"
    TIMESTAMP_OUT_OF_RANGE = "timestamp_out_of_range"
    PROTOBUF_PARSE_ERROR = "protobuf_parse_error"
    UNKNOWN_ROUTE = "unknown_route"  # applied in a later phase


class QuarantinedRow(BaseModel):
    row: dict[str, Any]
    reason: QuarantineReason
    error: str


def _reason_for(exc: ValidationError) -> QuarantineReason:
    errors = exc.errors()
    for err in errors:
        if err["type"] == ERR_COORDINATES:
            return QuarantineReason.INVALID_COORDINATES
        if err["type"] == ERR_TIMESTAMP:
            return QuarantineReason.TIMESTAMP_OUT_OF_RANGE
    for err in errors:
        loc = err["loc"][0] if err["loc"] else None
        if loc == "trip_id":
            return QuarantineReason.MISSING_TRIP_ID
        if loc == "stop_id" and err["type"] in {"missing", "string_type", "string_too_short"}:
            return QuarantineReason.MISSING_STOP_ID
    return QuarantineReason.SCHEMA_ERROR


def validate_records[M: BaseModel](
    raw_rows: list[dict[str, Any]],
    model: type[M],
    context: dict[str, Any] | None = None,
) -> tuple[list[M], list[QuarantinedRow]]:
    """Validate rows against model; split into valid models and quarantined rows."""
    ctx = context or {}
    if issubclass(model, VehiclePositionRecord) and "bbox" not in ctx:
        raise ValueError("vehicle position validation requires 'bbox' in context")
    valid: list[M] = []
    quarantined: list[QuarantinedRow] = []
    for row in raw_rows:
        try:
            valid.append(model.model_validate(row, context=ctx))
        except ValidationError as exc:
            quarantined.append(QuarantinedRow(row=row, reason=_reason_for(exc), error=str(exc)))
    return valid, quarantined
