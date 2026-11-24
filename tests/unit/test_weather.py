"""Daily weather: day classification, parsing, idempotent writes, handler gating."""

import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

from headway.config import load_city_config
from headway.curate import handler, weather
from headway.curate.weather import WeatherDay
from headway.storage import LocalStorage

CONFIG = load_city_config("boston")
D = date(2026, 1, 21)


@pytest.mark.parametrize(
    ("snow", "precip", "tmax", "flags"),
    [
        (25.4, 30.0, 1.0, (True, False, False)),  # snow day: its precipitation is not heavy rain
        (25.3, 5.0, 1.0, (False, False, False)),
        (0.0, 12.7, 20.0, (False, True, False)),
        (0.0, 12.6, 20.0, (False, False, False)),
        (0.0, 0.0, 32.2, (False, False, True)),
        (0.0, 0.0, 32.1, (False, False, False)),
        (0.0, 20.0, 33.0, (False, True, True)),  # heat and rain are independent
    ],
)
def test_day_classification(
    snow: float, precip: float, tmax: float, flags: tuple[bool, bool, bool]
) -> None:
    day = WeatherDay(D, snow, precip, tmax)
    assert (day.snow_flag, day.heavy_rain_flag, day.extreme_heat_flag) == flags


def test_missing_values_give_null_flags_not_false() -> None:
    day = WeatherDay(D, None, 20.0, 35.0)
    assert day.snow_flag is None
    assert day.heavy_rain_flag is None  # cannot tell it from a snow day
    assert day.extreme_heat_flag is True
    assert WeatherDay(D, 0.0, None, None).extreme_heat_flag is None


def test_parse_days_sorts_and_drops_empty_days() -> None:
    payload = [
        {"DATE": "2026-01-22", "STATION": "X", "TMAX": "7.8", "PRCP": "0.5", "SNOW": "0.0"},
        {"DATE": "2026-01-21", "STATION": "X", "TMAX": "1.7", "PRCP": "0.5"},
        {"DATE": "2026-01-23", "STATION": "X"},
    ]
    days = weather.parse_days(payload)
    assert [d.date for d in days] == [date(2026, 1, 21), date(2026, 1, 22)]
    assert days[0].snowfall_mm is None and days[0].tmax_c == 1.7
    with pytest.raises(ValueError):
        weather.parse_days({"error": "x"})


def _client(
    payload: Any, seen: list[httpx.Request] | None = None, status: int = 200
) -> httpx.Client:
    def respond(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, content=json.dumps(payload).encode())

    return httpx.Client(transport=httpx.MockTransport(respond))


PAYLOAD = [
    {"DATE": "2026-01-21", "SNOW": "30.0", "PRCP": "25.0", "TMAX": "1.7"},
    {"DATE": "2026-01-22", "SNOW": "0.0", "PRCP": "0.5", "TMAX": "7.8"},
]


def test_update_writes_one_part_per_date_and_is_idempotent(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    seen: list[httpx.Request] = []
    out = weather.update_weather(storage, CONFIG, _client(PAYLOAD, seen), date(2026, 1, 25))
    assert out == {"status": "ok", "days": 2}
    params = seen[0].url.params
    assert params["stations"] == "USW00014739" and params["startDate"] == "2026-01-04"
    keys = storage.list_keys("curated/boston/table=weather_daily/")
    assert len(keys) == 2
    row = pq.read_table(io.BytesIO(storage.get_bytes(keys[0]))).to_pylist()[0]
    assert row["snow_flag"] is True and row["heavy_rain_flag"] is False
    assert row["source"] == "ncei_ghcnd"

    weather.update_weather(storage, CONFIG, _client(PAYLOAD), date(2026, 1, 25))
    assert storage.list_keys("curated/boston/table=weather_daily/") == keys


def test_update_never_raises(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)
    bad = weather.update_weather(storage, CONFIG, _client([], status=503), date(2026, 1, 25))
    assert bad["status"] == "error"
    junk = weather.update_weather(storage, CONFIG, _client({"x": 1}), date(2026, 1, 25))
    assert junk["status"] == "error"
    no_station = CONFIG.model_copy(update={"weather_station": None})
    skipped = weather.update_weather(storage, no_station, _client(PAYLOAD), date(2026, 1, 25))
    assert skipped["status"] == "skipped"
    assert storage.list_keys("curated/") == []


def test_static_handler_fetches_weather_only_with_r3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[date] = []

    def fake_update(storage: Any, config: Any, client: Any, today: date) -> dict[str, Any]:
        calls.append(today)
        return {"status": "ok", "days": 0}

    root = Path(__file__).resolve().parents[2]
    import io as _io
    import zipfile

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in sorted((root / "samples" / "boston" / "gtfs").glob("*.txt")):
            zf.write(path, path.name)
    monkeypatch.setenv("CITY_ID", "boston")
    monkeypatch.setenv("HEADWAY_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(handler, "_download", lambda url: buf.getvalue())
    monkeypatch.setattr(handler, "_now", lambda: datetime(2026, 11, 22, 15, 5, tzinfo=UTC))
    monkeypatch.setattr(handler, "update_weather", fake_update)

    monkeypatch.delenv("HEADWAY_ENABLE_R3", raising=False)
    assert "weather" not in handler.static_handler({}, None) and calls == []
    monkeypatch.setenv("HEADWAY_ENABLE_R3", "true")
    summary = handler.static_handler({}, None)
    assert summary["weather"] == {"status": "ok", "days": 0} and calls == [date(2026, 11, 22)]
