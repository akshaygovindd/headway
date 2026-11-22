"""Freshness of the live snapshot and of the exported gold tables."""

from datetime import UTC, datetime

from lib.timefmt import parse_utc

STALE_SNAPSHOT_SECONDS = 180


def seconds_since(ts: str | datetime, now: datetime | None = None) -> float:
    """Seconds between `ts` and `now` (default: the current UTC time); never negative."""
    current = now if now is not None else datetime.now(UTC)
    return max((current - parse_utc(ts)).total_seconds(), 0.0)


def snapshot_age(snapshot: dict[str, object], now: datetime | None = None) -> float:
    return seconds_since(str(snapshot["generated_at"]), now)


def gold_age(manifest: dict[str, object], now: datetime | None = None) -> float:
    return seconds_since(str(manifest["exported_at"]), now)


def is_snapshot_stale(age_s: float) -> bool:
    return age_s > STALE_SNAPSHOT_SECONDS


def format_age(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h {s % 3600 // 60} min ago"
    return f"{s // 86400} d {s % 86400 // 3600} h ago"
