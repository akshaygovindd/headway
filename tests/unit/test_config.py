from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from headway.config import CityConfig, load_city_config


def _write(tmp_path: Path, data: dict[str, Any]) -> Path:
    (tmp_path / "test.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return tmp_path


def _boston_dict() -> dict[str, Any]:
    cfg = load_city_config("boston")
    return yaml.safe_load(cfg.model_dump_json())


def test_loads_boston() -> None:
    cfg = load_city_config("boston")
    assert isinstance(cfg, CityConfig)
    assert cfg.city_id == "boston"
    assert cfg.timezone == "America/New_York"
    assert cfg.service_day_cutoff_hour == 3
    assert cfg.route_types == [0, 1]
    assert "Green-B" in cfg.lines


def test_rejects_invalid_timezone(tmp_path: Path) -> None:
    data = _boston_dict()
    data["timezone"] = "Mars/Olympus"
    with pytest.raises(ValidationError):
        load_city_config("test", _write(tmp_path, data))


def test_rejects_missing_feed(tmp_path: Path) -> None:
    data = _boston_dict()
    del data["feeds"]["alerts"]
    with pytest.raises(ValidationError):
        load_city_config("test", _write(tmp_path, data))


def test_rejects_unknown_key(tmp_path: Path) -> None:
    data = _boston_dict()
    data["surprise"] = 1
    with pytest.raises(ValidationError):
        load_city_config("test", _write(tmp_path, data))


def test_rejects_inverted_bbox(tmp_path: Path) -> None:
    data = _boston_dict()
    data["bbox"]["lat_min"], data["bbox"]["lat_max"] = 43.0, 41.0
    with pytest.raises(ValidationError):
        load_city_config("test", _write(tmp_path, data))
