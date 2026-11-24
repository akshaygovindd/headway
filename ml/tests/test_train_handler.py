import json
import re
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pandas as pd
import pytest
from headway_ml import registry, state_data, train_handler
from moto import mock_aws
from synth import make_examples

BUCKET = "raw"
ROOT = Path(__file__).parents[2]


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    for k, v in {"AWS_ACCESS_KEY_ID": "x", "AWS_SECRET_ACCESS_KEY": "x",
                 "AWS_DEFAULT_REGION": "us-east-1", "RAW_BUCKET": BUCKET,
                 "HEADWAY_TRAIN_WORKDIR": str(tmp_path / "work")}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(train_handler, "WORKDIR", tmp_path / "work")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        yield client


def seed(s3: Any, tmp: Path, *, gate_days: int) -> None:
    df = make_examples(weeks=3)
    con = duckdb.connect()
    for day, part in df.groupby("service_date"):
        part = part.drop(columns=["period_type"])
        path = tmp / "p.parquet"
        con.register("part", part)
        con.execute(f"copy part to '{path}' (format parquet)")
        con.unregister("part")
        s3.upload_file(
            str(path), BUCKET,
            f"state/forecast_examples/metric_version=1/service_date={day}/part.parquet",
        )  # fmt: skip
    for i, d in enumerate(sorted(df["service_date"].unique())):
        gate = pd.DataFrame(
            {
                "line": ["Red", "Blue"],
                "service_date": d,
                "day_type": "weekend" if d.weekday() >= 5 else "weekday",
                "meets_coverage": i < gate_days,
            }
        )
        con.register("g", gate)
        path = tmp / "g.parquet"
        con.execute(f"copy g to '{path}' (format parquet)")
        con.unregister("g")
        s3.upload_file(
            str(path), BUCKET,
            f"state/forecast_gate_days/metric_version=1/service_date={d}/part.parquet",
        )  # fmt: skip


def test_state_versions_match_the_transform() -> None:
    src = (ROOT / "dbt/headway_transform/forecast_state.py").read_text()
    for name, version in (state_data.EXAMPLES, state_data.GATE_DAYS):
        assert re.search(rf'"{name}", {version}\)', src), name


def test_gate_not_passed_logs_baselines_and_writes_no_model(s3: Any, tmp_path: Path) -> None:
    seed(s3, tmp_path, gate_days=5)
    out = train_handler.lambda_handler({}, None)
    assert out["status"] == "baselines_only" and out["model_version"] is None
    assert out["mlflow_runs"] == 1 and set(out["gate_reasons"]) == {"Red", "Blue"}
    assert not any(k.startswith("models/") for k in out["model_objects"])
    s3.head_object(Bucket=BUCKET, Key="mlflow/mlflow.db")


def test_gate_passed_writes_the_model_and_the_db_round_trips(s3: Any, tmp_path: Path) -> None:
    seed(s3, tmp_path, gate_days=21)
    out = train_handler.lambda_handler({}, None)
    assert out["status"] == "trained" and out["model_version"] == 1
    assert out["gate_passed_lines"] == ["Blue", "Red"] and out["mlflow_runs"] == 1
    latest = json.loads(s3.get_object(Bucket=BUCKET, Key=registry.LATEST)["Body"].read())
    assert latest["version"] == 1
    # every invocation starts from an empty directory: the db comes back from S3
    again = train_handler.lambda_handler({}, None)
    assert again["model_version"] == 2 and again["mlflow_runs"] == 2


def test_no_data_is_reported_not_trained(s3: Any) -> None:
    assert train_handler.lambda_handler({}, None)["status"] == "no_data"


def test_loading_samples_nothing(s3: Any, tmp_path: Path) -> None:
    seed(s3, tmp_path, gate_days=21)
    ex, _ = state_data.load(s3, BUCKET, tmp_path / "a", 56)
    assert len(ex) == len(make_examples(weeks=3))


def test_handler_caps_training_and_logs_rows_per_line(
    s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(s3, tmp_path, gate_days=21)
    monkeypatch.setenv("TRAIN_MAX_ROWS_PER_DAY", "60")
    out = train_handler.lambda_handler({}, None)
    assert out["status"] == "trained"
    assert set(out["train_rows_by_line"]) == {"Blue", "Red"}
    assert all(0 < n <= 60 * 21 for n in out["train_rows_by_line"].values())
