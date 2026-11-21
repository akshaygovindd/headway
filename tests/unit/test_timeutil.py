from datetime import UTC, date, datetime

import pytest

from headway.timeutil import gtfs_time_to_utc, service_date_for

TZ = "America/New_York"


def utc(*a: int) -> datetime:
    return datetime(*a, tzinfo=UTC)


def test_cutoff_boundary() -> None:
    # 06:59 UTC = 02:59 EST (before 03:00 cutoff) -> previous day
    assert service_date_for(utc(2026, 1, 15, 7, 59), TZ, 3) == date(2026, 1, 14)
    assert service_date_for(utc(2026, 1, 15, 8, 0), TZ, 3) == date(2026, 1, 15)


def test_uses_local_date_not_utc() -> None:
    # 03:30 UTC Jan 16 = 22:30 EST Jan 15
    assert service_date_for(utc(2026, 1, 16, 3, 30), TZ, 3) == date(2026, 1, 15)


def test_service_date_requires_aware() -> None:
    with pytest.raises(ValueError):
        service_date_for(datetime(2026, 1, 1), TZ, 3)


def test_normal_day() -> None:
    assert gtfs_time_to_utc(date(2026, 1, 15), "08:00:00", TZ) == utc(2026, 1, 15, 13)


def test_past_24h() -> None:
    assert gtfs_time_to_utc(date(2026, 1, 15), "25:10:00", TZ) == utc(2026, 1, 16, 6, 10)


def test_spring_forward() -> None:
    d = date(2026, 3, 8)  # EST->EDT at 02:00 local
    # base = noon EDT(16:00Z) - 12h = 04:00Z (midnight EST)
    assert gtfs_time_to_utc(d, "00:00:00", TZ) == utc(2026, 3, 8, 4)
    assert gtfs_time_to_utc(d, "01:59:59", TZ) == utc(2026, 3, 8, 5, 59, 59)
    # GTFS clock is 1h behind wall clock after the transition
    assert gtfs_time_to_utc(d, "03:00:00", TZ) == utc(2026, 3, 8, 7)
    assert gtfs_time_to_utc(d, "12:00:00", TZ) == utc(2026, 3, 8, 16)
    assert gtfs_time_to_utc(d, "25:10:00", TZ) == utc(2026, 3, 9, 5, 10)


def test_fall_back() -> None:
    d = date(2026, 11, 1)  # EDT->EST at 02:00 local
    assert gtfs_time_to_utc(d, "00:00:00", TZ) == utc(2026, 11, 1, 5)
    assert gtfs_time_to_utc(d, "12:00:00", TZ) == utc(2026, 11, 1, 17)
    assert gtfs_time_to_utc(d, "25:10:00", TZ) == utc(2026, 11, 2, 6, 10)


def test_result_is_utc() -> None:
    assert gtfs_time_to_utc(date(2026, 1, 15), "08:00:00", TZ).tzinfo is UTC


@pytest.mark.parametrize("bad", ["8:00", "aa:bb:cc", "10:61:00", ""])
def test_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        gtfs_time_to_utc(date(2026, 1, 15), bad, TZ)
