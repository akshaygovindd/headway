"""Daily weather for the weather comparison (Release 3, ADR-022).

Source: NOAA NCEI Access Data Service, dataset ``daily-summaries`` (GHCN-Daily), station
USW00014739 (Boston Logan, the KBOS airport station). No key, public-domain data. The values for
a day arrive about a week late and can be corrected, so each run refetches the last
``REFETCH_DAYS`` days and rewrites one Parquet part per date (idempotent).

Day classes (descriptive thresholds, metric units; a flag is null when its value is missing):

* snow day: snowfall of at least 25.4 mm (1 inch);
* heavy-rain day: precipitation of at least 12.7 mm (0.5 inch) on a day that is not a snow day;
* extreme-heat day: daily maximum temperature of at least 32.2 C (90 F).
"""

import io
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from headway.config import CityConfig
from headway.curate.schemas import WEATHER_SCHEMA
from headway.storage import Storage

logger = logging.getLogger(__name__)

SOURCE = "ncei_ghcnd"
URL = "https://www.ncei.noaa.gov/access/services/data/v1"
REFETCH_DAYS = 21
TIMEOUT_S = 30.0
SNOW_MM = 25.4
HEAVY_RAIN_MM = 12.7
EXTREME_HEAT_C = 32.2


@dataclass(frozen=True)
class WeatherDay:
    date: date
    snowfall_mm: float | None
    precip_mm: float | None
    tmax_c: float | None

    @property
    def snow_flag(self) -> bool | None:
        return None if self.snowfall_mm is None else self.snowfall_mm >= SNOW_MM

    @property
    def heavy_rain_flag(self) -> bool | None:
        if self.precip_mm is None or self.snow_flag is None:
            return None
        return self.precip_mm >= HEAVY_RAIN_MM and not self.snow_flag

    @property
    def extreme_heat_flag(self) -> bool | None:
        return None if self.tmax_c is None else self.tmax_c >= EXTREME_HEAT_C


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_days(payload: Any) -> list[WeatherDay]:
    """Rows of the daily-summaries JSON (metric units); days with no value at all are dropped."""
    if not isinstance(payload, list):
        raise ValueError("unexpected daily-summaries payload")
    days = []
    for row in payload:
        day = WeatherDay(
            date.fromisoformat(str(row["DATE"])[:10]),
            _num(row.get("SNOW")),
            _num(row.get("PRCP")),
            _num(row.get("TMAX")),
        )
        if day.snowfall_mm is None and day.precip_mm is None and day.tmax_c is None:
            continue
        days.append(day)
    return sorted(days, key=lambda d: d.date)


def fetch_days(
    client: httpx.Client, station: str, start: date, end: date, *, url: str = URL
) -> list[WeatherDay]:
    response = client.get(
        url,
        params={
            "dataset": "daily-summaries",
            "stations": station,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dataTypes": "PRCP,SNOW,TMAX",
            "units": "metric",
            "format": "json",
        },
        timeout=TIMEOUT_S,
    )
    response.raise_for_status()
    return parse_days(response.json())


def weather_key(prefix: str, day: date) -> str:
    return f"curated/{prefix}/table=weather_daily/dt={day.isoformat()}/part.parquet"


def write_days(storage: Storage, config: CityConfig, days: list[WeatherDay]) -> int:
    """One Parquet part per date, overwritten on a refetch. Returns the number written."""
    for d in days:
        table = pa.Table.from_pylist(
            [
                {
                    "date": d.date,
                    "snow_flag": d.snow_flag,
                    "heavy_rain_flag": d.heavy_rain_flag,
                    "extreme_heat_flag": d.extreme_heat_flag,
                    "snowfall_mm": d.snowfall_mm,
                    "precip_mm": d.precip_mm,
                    "tmax_c": d.tmax_c,
                    "source": SOURCE,
                }
            ],
            schema=WEATHER_SCHEMA,
        )
        buf = io.BytesIO()
        pq.write_table(table, buf)
        storage.put_bytes(weather_key(config.storage_prefix, d.date), buf.getvalue())
    return len(days)


def update_weather(
    storage: Storage, config: CityConfig, client: httpx.Client, today: date
) -> dict[str, Any]:
    """Refetch the recent window and write it; never raises (a failure is reported, not fatal)."""
    if not config.weather_station:
        return {"status": "skipped", "reason": "no weather_station in the city config"}
    try:
        days = fetch_days(
            client, config.weather_station, today - timedelta(days=REFETCH_DAYS), today
        )
        return {"status": "ok", "days": write_days(storage, config, days)}
    except Exception as exc:  # network, HTTP status, malformed payload, storage: GTFS load goes on
        logger.warning("weather update failed: %s: %s", type(exc).__name__, exc)
        return {"status": "error", "error": type(exc).__name__}
