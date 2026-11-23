"""export_gold to s3:// through dbt-duckdb httpfs, against a moto S3 server (no AWS access)."""

import json
import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto.server import ThreadedMotoServer

from headway_transform import handler, publish

ROOT = Path(__file__).resolve().parents[2]
BUCKET = "public-bucket"
PREFIX = "public/boston/gold"
DBT = ["uv", "run", "--project", "dbt", "dbt"]


@pytest.fixture(scope="module")
def moto_endpoint() -> Iterator[str]:
    server = ThreadedMotoServer(port=0)
    server.start()
    host, port = server.get_host_and_port()
    yield f"{host}:{port}"
    server.stop()


def _dbt(args: list[str], env: dict[str, str], curated: Path) -> subprocess.CompletedProcess[str]:
    variables = json.dumps({"curated_root": str(curated)})
    flags = ["--project-dir", "dbt/headway", "--profiles-dir", "dbt/headway", "--target", "s3_test"]
    return subprocess.run(
        [*DBT, *args, *flags, "--vars", variables],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory, moto_endpoint: str) -> dict[str, Any]:
    if shutil.which("uv") is None:
        pytest.skip("uv is required to run dbt")
    work = tmp_path_factory.mktemp("s3export")
    curated = work / "curated"
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    env.update(
        AWS_ACCESS_KEY_ID="test",
        AWS_SECRET_ACCESS_KEY="test",
        AWS_DEFAULT_REGION="us-east-1",
        HEADWAY_S3_ENDPOINT=moto_endpoint,
        HEADWAY_DUCKDB_PATH=str(work / "headway.duckdb"),
    )
    subprocess.run(
        ["uv", "run", "headway", "replay", "--source", "samples/boston", "--target", str(curated),
         "--city", "boston"],
        cwd=ROOT, env=env, check=True, capture_output=True,
    )  # fmt: skip
    client = boto3.client("s3", endpoint_url=f"http://{moto_endpoint}", **_creds())
    client.create_bucket(Bucket=BUCKET)
    build = _dbt(["build"], env, curated)
    export = _dbt(
        ["run-operation", "export_gold", "--args",
         json.dumps({"target_dir": f"s3://{BUCKET}/{PREFIX}"})],
        env, curated,
    )  # fmt: skip
    return {"s3": client, "build": build, "export": export}


def _creds() -> dict[str, str]:
    return {
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
        "region_name": "us-east-1",
    }


def test_export_writes_every_allowlisted_object(exported: dict[str, Any]) -> None:
    assert exported["export"].returncode == 0, exported["export"].stdout[-2000:]
    keys = {
        o["Key"] for o in exported["s3"].list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)["Contents"]
    }
    assert keys == {f"{PREFIX}/{t}.parquet" for t in publish.GOLD_TABLES} | {
        f"{PREFIX}/manifest.json"
    }


def test_manifest_row_counts_cover_the_allowlist(exported: dict[str, Any]) -> None:
    body = exported["s3"].get_object(Bucket=BUCKET, Key=f"{PREFIX}/manifest.json")["Body"].read()
    assert set(json.loads(body)["row_counts"]) == set(publish.GOLD_TABLES)


def test_verify_export_passes_on_the_exported_objects(exported: dict[str, Any]) -> None:
    handler.verify_export(exported["s3"], BUCKET, PREFIX)


def test_verify_export_names_missing_keys(exported: dict[str, Any]) -> None:
    with pytest.raises(handler.ExportError, match=r"gold/dim_stop\.parquet") as err:
        handler.verify_export(exported["s3"], BUCKET, "public/other/gold")
    assert "manifest.json" in str(err.value)
