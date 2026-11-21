"""Service-day and GTFS time helpers."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def service_date_for(ts_utc: datetime, tz: str, cutoff_hour: int) -> date:
    """Local date of ts_utc, minus one day if the local hour is before the cutoff."""
    if ts_utc.tzinfo is None:
        raise ValueError("ts_utc must be timezone-aware")
    local = ts_utc.astimezone(ZoneInfo(tz))
    day = local.date()
    if local.hour < cutoff_hour:
        day -= timedelta(days=1)
    return day


def gtfs_time_to_utc(service_date: date, hhmmss: str, tz: str) -> datetime:
    """Convert a GTFS time (hours may exceed 24) to UTC.

    GTFS measures time from "noon minus 12h" on the service date, which
    keeps the result correct across DST transitions.
    """
    parts = hhmmss.strip().split(":")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"invalid GTFS time: {hhmmss!r}")
    hours, minutes, seconds = (int(p) for p in parts)
    if minutes > 59 or seconds > 59:
        raise ValueError(f"invalid GTFS time: {hhmmss!r}")
    zone = ZoneInfo(tz)
    noon = datetime.combine(service_date, time(12), tzinfo=zone)
    noon_utc = noon.astimezone(UTC)
    base = noon_utc - timedelta(hours=12)
    return base + timedelta(hours=hours, minutes=minutes, seconds=seconds)
