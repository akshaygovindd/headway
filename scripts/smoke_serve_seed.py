"""Seed a (moto) raw bucket with a registered forecast model and the training job's reports, for
the serving smoke test of the transform image.

Runs in the ml environment (`uv run --project ml python scripts/smoke_serve_seed.py ...`): trains
the model on synthetic examples for the lines the sample serves (ml/tests/synth.py) under a passed
gate and writes models/forecast/v1/, latest.json and the reports with the code the training job
uses (registry.py, reports.py).
"""

import argparse
import sys
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml"))
from headway_ml import registry, reports, train  # noqa: E402
from synth import make_examples, make_gate  # noqa: E402

LINES = ("Red", "Orange", "Blue")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    args = parser.parse_args()
    s3 = boto3.client(
        "s3", endpoint_url=args.endpoint, region_name="us-east-1",
        aws_access_key_id="smoke", aws_secret_access_key="smoke",
    )  # fmt: skip
    s3.create_bucket(Bucket=args.bucket)
    gate = make_gate(LINES, passed=LINES)
    result = train.run_training(make_examples(weeks=3, lines=LINES), gate, git_sha="smoke", reps=30)
    assert result.artifact is not None
    version = registry.write_model(s3, args.bucket, result.artifact, gate)
    reports.write(s3, args.bucket, "smoke-run", result.backtest.results, gate)
    print(f"seeded s3://{args.bucket}: model v{version} for {sorted(result.passed)}")


if __name__ == "__main__":
    main()
