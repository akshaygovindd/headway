import re
from pathlib import Path
from typing import Any

import boto3
import lightgbm as lgb
import pandas as pd
import pytest
from headway_ml import data, registry, train
from moto import mock_aws
from synth import make_examples, make_gate

BUCKET = "raw"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        yield client


def keys(s3: Any) -> list[str]:
    out = s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    return sorted(o["Key"] for o in out)


def _train(gate: pd.DataFrame) -> train.TrainResult:
    return train.run_training(make_examples(weeks=3), gate, git_sha="abc1234", reps=10)


def test_gate_not_passed_trains_nothing_and_writes_nothing(s3: Any) -> None:
    res = _train(make_gate())
    assert res.artifact is None and res.passed == []
    assert any("no line passed the training gate" in n for n in res.notes)
    assert set(res.reasons) == {"Red", "Blue"}
    assert "lightgbm" not in set(res.backtest.results["method"])  # baselines only
    with pytest.raises(registry.GateError):
        registry.write_model(
            s3, BUCKET,
            registry.ModelArtifact("m", ["Red"], [], {}, [], "abc"),
            make_gate(),
        )  # fmt: skip
    assert keys(s3) == []


def test_mixed_gate_trains_on_the_passed_line_only(s3: Any) -> None:
    gate = make_gate(passed=("Red",))
    res = _train(gate)
    assert res.artifact is not None and res.artifact.lines == ["Red"]
    assert "Blue" in res.reasons and "Blue" not in res.passed
    with pytest.raises(registry.GateError):  # the gate holds in the registry too
        registry.write_model(
            s3, BUCKET, registry.ModelArtifact("m", ["Red", "Blue"], [], {}, [], "abc"), gate
        )
    assert keys(s3) == []


def test_versions_increment_and_latest_follows(s3: Any, tmp_path: Path) -> None:
    gate = make_gate(passed=("Red", "Blue"))
    res = _train(gate)
    assert res.artifact is not None
    assert registry.write_model(s3, BUCKET, res.artifact, gate) == 1
    assert registry.write_model(s3, BUCKET, res.artifact, gate) == 2
    import json

    latest = json.loads(s3.get_object(Bucket=BUCKET, Key=registry.LATEST)["Body"].read())
    assert latest["version"] == 2 and latest["lines"] == ["Blue", "Red"]
    assert latest["git_sha"] == "abc1234"
    assert {k for k in keys(s3) if k.startswith("models/forecast/v2/")} == {
        f"models/forecast/v2/{n}"
        for n in ("model.txt", "lines.json", "features.json", "window.json", "metrics.json",
                  "meta.json")
    }  # fmt: skip
    path = tmp_path / "model.txt"
    path.write_bytes(
        s3.get_object(Bucket=BUCKET, Key="models/forecast/v2/model.txt")["Body"].read()
    )
    booster = lgb.Booster(model_file=str(path))
    assert booster.num_trees() > 0


def test_a_failed_write_does_not_move_latest(s3: Any) -> None:
    gate = make_gate(passed=("Red",))
    res = _train(gate)
    assert res.artifact is not None
    registry.write_model(s3, BUCKET, res.artifact, gate)
    before = s3.get_object(Bucket=BUCKET, Key=registry.LATEST)["Body"].read()
    bad = registry.ModelArtifact("m", ["Blue"], [], {}, [], "abc")
    with pytest.raises(registry.GateError):
        registry.write_model(s3, BUCKET, bad, gate)
    assert s3.get_object(Bucket=BUCKET, Key=registry.LATEST)["Body"].read() == before


def test_gate_from_days_matches_the_dbt_rule() -> None:
    rows = []
    for line, n_weekend in (("Red", 3), ("Blue", 2)):
        for d in range(24):
            rows.append({"line": line, "service_date": d, "meets_coverage": True,
                         "day_type": "weekend" if d < n_weekend else "weekday"})  # fmt: skip
    rows.append({"line": "Red", "service_date": 99, "meets_coverage": False, "day_type": "weekday"})
    gate = data.gate_from_days(pd.DataFrame(rows)).set_index("line")
    assert gate.loc["Red", "n_days"] == 24 and bool(gate.loc["Red", "gate_passed"])
    assert not bool(gate.loc["Blue", "gate_passed"])  # 2 weekend days < 3


def test_gate_constants_match_the_dbt_project() -> None:
    text = (Path(__file__).parents[2] / "dbt/headway/dbt_project.yml").read_text()
    block = text[text.index("forecast_gate:") :]
    got = {k: int(v) for k, v in re.findall(r"(min_\w+):\s*(\d+)", block[:200])}
    assert got == {
        "min_coverage_pct": data.GATE_MIN_COVERAGE_PCT,
        "min_days": data.GATE_MIN_DAYS,
        "min_weekend_days": data.GATE_MIN_WEEKEND_DAYS,
    }
