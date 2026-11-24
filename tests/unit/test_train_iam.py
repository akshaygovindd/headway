"""The training role and the transform role allow exactly the keys the forecast state uses."""

import fnmatch
import re
from datetime import date
from pathlib import Path

from headway_transform import forecast_state

INFRA = Path(__file__).resolve().parents[2] / "infra" / "main"
ML = Path(__file__).resolve().parents[2] / "ml" / "headway_ml"
TRAIN_TF = (INFRA / "train.tf").read_text()
PIPELINE = (INFRA / "pipeline.tf").read_text()


def _patterns(sid: str) -> list[str]:
    start = re.search(rf'sid\s+=\s+"{sid}"', TRAIN_TF)
    assert start, sid
    body = TRAIN_TF[start.start() :].split("\n  }\n", 1)[0]
    return re.findall(r'\}/([^"]+)"', body) or re.findall(r'"((?:state|mlflow|models)[^"]*)"', body)


def _const(path: str, name: str) -> str:
    return re.search(rf'^{name}\s*=\s*"([^"]+)"', (ML / path).read_text(), re.M).group(1)  # type: ignore[union-attr]


def test_the_training_role_reads_the_partitions_the_transform_writes() -> None:
    read = _patterns("ReadForecastState")
    for name, version in forecast_state.TABLES.values():
        key = forecast_state.key_for(name, version, date(2026, 11, 22))
        assert any(fnmatch.fnmatch(key, p) for p in read), key
    listing = _patterns("ListTrainingPrefixes")
    for name, version in forecast_state.TABLES.values():
        prefix = f"state/{name}/metric_version={version}/"
        assert any(fnmatch.fnmatch(prefix, p) for p in listing), prefix


def test_the_training_role_writes_only_mlflow_and_the_registry() -> None:
    write = _patterns("ReadWriteMlflowAndModels")
    keys = [
        _const("s3store.py", "DB_KEY"),
        _const("s3store.py", "LOCK_KEY"),
        "mlflow/artifacts/abc/artifacts/features.json",
        "models/forecast/latest.json",
        "models/forecast/v3/model.txt",
    ]
    for key in keys:
        assert any(fnmatch.fnmatch(key, p) for p in write), key
    assert not any(fnmatch.fnmatch("state/forecast_examples/x", p) for p in write)
    assert not any(fnmatch.fnmatch("curated/x", p) for p in write)
    assert 'resources = ["*"]' not in TRAIN_TF


def test_the_transform_role_covers_the_forecast_state_only_with_release_3() -> None:
    assert "var.enable_r3 ? [local.forecast_state_prefix, local.incident_state_prefix] : []" in PIPELINE
    assert 'forecast_state_prefix = "state/forecast_*"' in PIPELINE
    for name, version in forecast_state.TABLES.values():
        key = forecast_state.key_for(name, version, date(2026, 11, 22))
        assert fnmatch.fnmatch(key, "state/forecast_*/*"), key


def test_the_transform_role_reads_the_model_registry_only_with_release_3() -> None:
    assert "var.enable_r3 ? [local.forecast_model_prefix] : []" in PIPELINE
    assert 'forecast_model_prefix = "models/forecast/*"' in PIPELINE
    for key in (
        "models/forecast/latest.json",
        "models/forecast/v2/model.txt",
        "models/forecast/reports/backtest.parquet",
    ):
        assert fnmatch.fnmatch(key, "models/forecast/*"), key
    # read and list only: the transform never writes the registry
    writes = re.findall(r'sid\s*=\s*"WriteRunState".*?resources[^\n]*\n', PIPELINE, re.S)
    assert writes and "forecast_model_prefix" not in writes[0]


def test_every_train_resource_is_behind_enable_r3() -> None:
    resources = re.findall(r'^(resource|data) "[^"]+" "[^"]+" \{\n(.*?)\n\}', TRAIN_TF, re.M | re.S)
    assert resources
    for _, body in resources:
        assert "count = var.enable_r3 ? 1 : 0" in body
