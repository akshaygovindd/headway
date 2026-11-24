"""Seed a (moto) raw bucket with synthetic closed-day partitions for the training smoke test.

Runs in the ml environment (`uv run --project ml python scripts/smoke_train_seed.py ...`):
three weeks of examples for two lines (ml/tests/synth.py) and per line x day gate rows of which
`--gate-days` count toward the gate. The same keys the transform writes (forecast_state.py).
"""

import argparse
import sys
import tempfile
from pathlib import Path

import boto3
import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ml" / "tests"))
from synth import make_examples  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--gate-days", type=int, required=True)
    args = parser.parse_args()
    s3 = boto3.client(
        "s3", endpoint_url=args.endpoint, region_name="us-east-1",
        aws_access_key_id="smoke", aws_secret_access_key="smoke",
    )  # fmt: skip
    s3.create_bucket(Bucket=args.bucket)
    examples = make_examples(weeks=3).drop(columns=["period_type"])
    con = duckdb.connect()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "part.parquet"
        for i, (day, part) in enumerate(examples.groupby("service_date")):
            con.register("part", part)
            con.execute(f"copy part to '{path}' (format parquet)")
            con.unregister("part")
            base = f"state/%s/metric_version=1/service_date={day}/part.parquet"
            s3.upload_file(str(path), args.bucket, base % "forecast_examples")
            gate = pd.DataFrame(
                {
                    "line": ["Red", "Blue"],
                    "service_date": day,
                    "day_type": "weekend" if day.weekday() >= 5 else "weekday",
                    "meets_coverage": i < args.gate_days,
                }
            )
            con.register("gate", gate)
            con.execute(f"copy gate to '{path}' (format parquet)")
            con.unregister("gate")
            s3.upload_file(str(path), args.bucket, base % "forecast_gate_days")
    print(f"seeded s3://{args.bucket}: {len(examples)} examples, gate days {args.gate_days}")


if __name__ == "__main__":
    main()
