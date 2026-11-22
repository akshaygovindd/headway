"""Flat record contracts for parsed GTFS-realtime rows.

Context-dependent rules read from the validation context:
``bbox`` (a ``headway.config.BBox``) is required for vehicle positions.
Rule violations raise custom error types that ``validate_records`` maps to
quarantine reasons.
"""

from datetime import timedelta
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticCustomError

from headway.config import BBox
from headway.contracts.types import UtcDatetime

PAST_WINDOW = timedelta(hours=6)
FUTURE_WINDOW = timedelta(hours=24)
MAX_EVENT_LAG = timedelta(minutes=10)

# Stop updates that legitimately carry no predicted time (the stop is skipped or has no data).
NO_PREDICTION_RELATIONSHIPS = frozenset({"SKIPPED", "NO_DATA"})

ERR_TIMESTAMP = "timestamp_out_of_range"
ERR_COORDINATES = "invalid_coordinates"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TripUpdateRecord(_Record):
    capture_id: str = Field(min_length=1)
    trip_id: str = Field(min_length=1)
    route_id: str | None = None
    direction_id: int | None = None
    start_date: str | None = None
    start_time: str | None = None
    vehicle_id: str | None = None
    stop_id: str = Field(min_length=1)
    stop_sequence: int | None = Field(default=None, ge=0)
    predicted_arrival_ts: UtcDatetime | None = None
    predicted_departure_ts: UtcDatetime | None = None
    uncertainty: int | None = None
    schedule_relationship: str | None = None
    event_ts: UtcDatetime | None = None
    feed_header_ts: UtcDatetime
    fetched_at: UtcDatetime

    @model_validator(mode="after")
    def _check_predictions(self) -> Self:
        times = [t for t in (self.predicted_arrival_ts, self.predicted_departure_ts) if t]
        if not times:
            if self.schedule_relationship in NO_PREDICTION_RELATIONSHIPS:
                return self
            raise ValueError("at least one predicted time is required")
        lo, hi = self.fetched_at - PAST_WINDOW, self.fetched_at + FUTURE_WINDOW
        if any(t < lo or t > hi for t in times):
            raise PydanticCustomError(
                ERR_TIMESTAMP, "predicted time outside fetched_at -6h/+24h window"
            )
        return self


class VehiclePositionRecord(_Record):
    capture_id: str = Field(min_length=1)
    vehicle_id: str = Field(min_length=1)
    trip_id: str | None = None
    route_id: str | None = None
    direction_id: int | None = None
    lat: float
    lon: float
    bearing: float | None = None
    speed: float | None = None
    current_status: str | None = None
    stop_id: str | None = None
    current_stop_sequence: int | None = None
    event_ts: UtcDatetime | None = None
    feed_header_ts: UtcDatetime
    fetched_at: UtcDatetime

    @field_validator("lat", "lon")
    @classmethod
    def _finite(cls, value: float) -> float:
        if value != value or abs(value) > 180:
            raise PydanticCustomError(ERR_COORDINATES, "coordinate is not a valid degree value")
        return value

    @model_validator(mode="after")
    def _check_bbox_and_lag(self, info: ValidationInfo) -> Self:
        ctx: dict[str, Any] = info.context or {}
        bbox: BBox | None = ctx.get("bbox")
        if bbox is not None and not (
            bbox.lat_min <= self.lat <= bbox.lat_max and bbox.lon_min <= self.lon <= bbox.lon_max
        ):
            raise PydanticCustomError(ERR_COORDINATES, "position outside city bounding box")
        if self.event_ts is not None and self.feed_header_ts - self.event_ts > MAX_EVENT_LAG:
            raise PydanticCustomError(ERR_TIMESTAMP, "event_ts more than 10 minutes before header")
        return self


class AlertRecord(_Record):
    capture_id: str = Field(min_length=1)
    alert_id: str = Field(min_length=1)
    entity_index: int = Field(ge=0)
    effect: str | None = None
    cause: str | None = None
    route_id: str | None = None
    stop_id: str | None = None
    active_start: UtcDatetime | None = None
    active_end: UtcDatetime | None = None
    header_text: str | None = None
    feed_header_ts: UtcDatetime
    fetched_at: UtcDatetime
