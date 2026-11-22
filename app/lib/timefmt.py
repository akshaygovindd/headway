"""Time handling. Everything is stored and computed in UTC; local time is for display only."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DISPLAY_TZ = ZoneInfo("America/New_York")
SERVICE_DAY_CUTOFF_HOUR = 3
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def parse_utc(value: str | datetime) -> datetime:
    """Parse an ISO-8601 timestamp (trailing Z allowed) into an aware UTC datetime."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def to_local(ts: str | datetime) -> datetime:
    """UTC instant to America/New_York wall time. Use at display time only."""
    return parse_utc(ts).astimezone(DISPLAY_TZ)


def format_local(ts: str | datetime) -> str:
    return to_local(ts).strftime("%Y-%m-%d %H:%M %Z")


def local_date(service_date: date, hour_local: int) -> date:
    """Calendar date of a service hour: hours before the cutoff fall on the next date."""
    return service_date + timedelta(days=1 if hour_local < SERVICE_DAY_CUTOFF_HOUR else 0)


def hour_start_utc(service_date: date, hour_local: int) -> datetime:
    """UTC start of the local hour `hour_local` of `service_date`."""
    day = local_date(service_date, hour_local)
    return datetime(day.year, day.month, day.day, hour_local, tzinfo=DISPLAY_TZ).astimezone(UTC)


def weekday_name(service_date: date, hour_local: int) -> str:
    return WEEKDAYS[local_date(service_date, hour_local).weekday()]


def date_label(value: date | datetime) -> str:
    """Plain YYYY-MM-DD string for chart axes (avoids timezone shifts in the browser)."""
    return value.strftime("%Y-%m-%d")


def hour_label(hour_local: int) -> str:
    return f"{hour_local:02d}:00"
