"""City configuration loading and validation."""

import os
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "cities"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Feeds(_Frozen):
    trip_updates: HttpUrl
    vehicle_positions: HttpUrl
    alerts: HttpUrl
    gtfs_static: HttpUrl


class BBox(_Frozen):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @model_validator(mode="after")
    def _check_order(self) -> Self:
        if self.lat_min >= self.lat_max:
            raise ValueError("lat_min must be less than lat_max")
        if self.lon_min >= self.lon_max:
            raise ValueError("lon_min must be less than lon_max")
        return self


class CityConfig(_Frozen):
    city_id: str
    agency: str
    timezone: str
    service_day_cutoff_hour: int = Field(ge=0, le=6)
    route_types: list[int]
    feeds: Feeds
    storage_prefix: str
    lines: list[str]
    bbox: BBox
    # GHCN-Daily station for the weather comparison (Release 3); none: no weather fetched.
    weather_station: str | None = None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone: {value}") from exc
        return value


def load_city_config(city_id: str, config_dir: Path | None = None) -> CityConfig:
    """Load and validate the YAML config for ``city_id``."""
    if config_dir is not None:
        directory = config_dir
    elif env_dir := os.environ.get("HEADWAY_CONFIG_DIR"):
        directory = Path(env_dir)
    else:
        directory = DEFAULT_CONFIG_DIR
    path = directory / f"{city_id}.yaml"
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return CityConfig.model_validate(data)
