"""Shared field types for data contracts."""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import AfterValidator

FeedName = Literal["trip_updates", "vehicle_positions", "alerts"]


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]
