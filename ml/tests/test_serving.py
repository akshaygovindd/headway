import json
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
import pytest
from headway_ml import model, registry, serving, train
from moto import mock_aws
from synth import make_examples, make_gate

BUCKET = "raw"


@pytest.fixture
def s3() -> Any:
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        serving.reset()
        yield client
        serving.reset()


def trained(passed: tuple[str, ...] = ("Red", "Blue")) -> tuple[pd.DataFrame, train.TrainResult]:
    df = make_examples(weeks=3)
    res = train.run_training(df, make_gate(passed=passed), git_sha="t", reps=20)
    assert res.artifact is not None
    return df, res


def test_no_latest_json_means_no_model(s3: Any, tmp_path: Path) -> None:
    assert serving.load_latest(s3, BUCKET, tmp_path) is None


def test_served_scores_equal_the_training_forecasters(s3: Any, tmp_path: Path) -> None:
    df, res = trained()
    assert res.artifact is not None
    registry.write_model(s3, BUCKET, res.artifact, make_gate(passed=("Red", "Blue")))
    rows = df[df["service_date"] == df["service_date"].max()].head(300)
    fc = model.LightGBMForecaster()
    fc.fit(df)  # same data, same seed: the same model as the artifact's
    served = serving.load_latest(s3, BUCKET, tmp_path)
    assert served is not None and served.version == 1 and served.lines == ["Blue", "Red"]
    # the booster read from the registry text predicts what the booster that wrote it predicts
    direct = pd.Series(fc.predict(rows))
    again = pd.Series(served.score(rows))
    assert len(again) == len(direct)
    assert (again - direct).abs().max() < 1e-6
    with_missing = rows.assign(active_disruption_alert=None)
    assert len(served.score(with_missing)) == len(rows)  # a missing alert flag reads as False


def test_warm_start_reads_only_latest_json_and_cold_start_uses_the_disk_cache(
    s3: Any, tmp_path: Path
) -> None:
    _, res = trained()
    assert res.artifact is not None
    registry.write_model(s3, BUCKET, res.artifact, make_gate(passed=("Red", "Blue")))
    first = serving.load_latest(s3, BUCKET, tmp_path)
    assert first is not None and not first.from_cache
    assert serving.load_latest(s3, BUCKET, tmp_path) is first  # warm: same object
    serving.reset()  # a new process on the same /tmp: files stay
    second = serving.load_latest(s3, BUCKET, tmp_path)
    assert second is not None and second.from_cache and second.version == 1
    assert json.loads((tmp_path / "v1" / "lines.json").read_text()) == ["Blue", "Red"]


def test_scoring_an_unknown_line_is_refused(s3: Any, tmp_path: Path) -> None:
    df, res = trained(passed=("Red",))
    assert res.artifact is not None
    registry.write_model(s3, BUCKET, res.artifact, make_gate(passed=("Red",)))
    served = serving.load_latest(s3, BUCKET, tmp_path)
    assert served is not None and served.lines == ["Red"]
    with pytest.raises(ValueError):
        served.score(df[df["line"] == "Blue"].head(5))
