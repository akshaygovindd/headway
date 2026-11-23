import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pyarrow.parquet as pq
import pytest
from moto import mock_aws

from headway.config import load_city_config
from headway.curate import handler
from headway.curate.pipeline import read_watermark
from headway.curate.replay import stage_inputs
from headway.storage import LocalStorage, S3Storage

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "boston"
CONFIG = load_city_config("boston")
NOW = datetime(2026, 11, 22, 15, 5, 0, tzinfo=UTC)


def _gtfs_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in sorted((SAMPLE / "gtfs").glob("*.txt")):
            zf.write(path, path.name)
    return buf.getvalue()


@pytest.fixture
def bucket(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("RAW_BUCKET", "raw-bucket")
    monkeypatch.setenv("CITY_ID", "boston")
    monkeypatch.setattr(handler, "_now", lambda: NOW)
    monkeypatch.setattr(handler, "_download", lambda url: _gtfs_zip())
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="raw-bucket")
        yield S3Storage("raw-bucket")


def _curated(storage: S3Storage, suffix: str = "") -> list[str]:
    return [
        k
        for k in storage.list_keys("curated/boston/table=")
        if k.endswith(suffix) and "/static/" not in k
    ]


def test_curate_round_trip_on_sample_data(bucket: S3Storage) -> None:
    stage_inputs(LocalStorage(SAMPLE), bucket, CONFIG)
    handler.static_handler({}, None)

    first = handler.curate_handler({}, None)
    assert first["task"] == "curate"
    assert first["rows"]["trip_updates"] > 0
    assert _curated(bucket, ".parquet")
    mark = read_watermark(bucket, "boston")
    assert mark is not None

    before = _curated(bucket)
    second = handler.curate_handler({}, None)
    assert second["batches"] == 0
    assert _curated(bucket) == before


def test_compact_task_merges_the_previous_hour(bucket: S3Storage) -> None:
    stage_inputs(LocalStorage(SAMPLE), bucket, CONFIG)
    handler.static_handler({}, None)
    handler.curate_handler({}, None)
    hour14 = [k for k in _curated(bucket) if "/hour=14/" in k]
    assert any("part-compacted" not in k for k in hour14)

    summary = handler.curate_handler({"task": "compact"}, None)
    assert summary["task"] == "compact"
    assert summary["hour"] == "2026-11-22T14:00:00+00:00"
    assert summary["inputs_removed"] > 0
    after = [k for k in _curated(bucket) if "/hour=14/" in k]
    assert after and all(k.endswith("part-compacted.parquet") for k in after)
    hour15 = [k for k in _curated(bucket) if "/hour=15/" in k]
    assert all("part-compacted" not in k for k in hour15)


def test_static_handler_loads_then_skips_known_version(bucket: S3Storage) -> None:
    first = handler.static_handler({}, None)
    assert first["skipped"] is False
    assert first["rows"]["routes"] > 0
    assert bucket.exists(first["raw_key"])
    assert first["raw_key"].startswith("raw/boston/feed=gtfs_static/dt=2026-11-22/hour=15/")

    second = handler.static_handler({}, None)
    assert second["skipped"] is True
    assert second["feed_version"] == first["feed_version"]
    assert second["rows"] == {}
    assert len(bucket.list_keys("curated/boston/static/table=feed_info/")) == 1


def test_summary_is_one_json_line(bucket: S3Storage, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    handler.static_handler({}, None)
    lines = [r.message for r in caplog.records if r.message.startswith("{")]
    assert json.loads(lines[-1])["task"] == "static"


def test_curate_writes_manifest_and_quarantine_tables(bucket: S3Storage) -> None:
    stage_inputs(LocalStorage(SAMPLE), bucket, CONFIG)
    handler.static_handler({}, None)
    handler.curate_handler({}, None)
    raw_entries = 3 * len(bucket.list_keys("manifests/boston/"))
    manifests = [k for k in _curated(bucket, ".parquet") if "/table=manifests/" in k]
    assert {k.split("/")[3] for k in manifests} == {"dt=2026-11-22"}
    assert sum(pq.read_metadata(io.BytesIO(bucket.get_bytes(k))).num_rows for k in manifests) == (
        raw_entries
    )
    assert any("/table=quarantine/" in k for k in _curated(bucket, ".parquet"))


def test_backfill_task_builds_the_tables_from_raw_objects(bucket: S3Storage) -> None:
    stage_inputs(LocalStorage(SAMPLE), bucket, CONFIG)
    first = handler.curate_handler({"task": "backfill_manifests"}, None)
    assert first["task"] == "backfill_manifests"
    assert first["manifest_rows"] == 3 * len(bucket.list_keys("manifests/boston/"))
    assert first["hours_rewritten"] > 0

    again = handler.curate_handler({"task": "backfill_manifests"}, None)
    assert again["manifest_rows"] == first["manifest_rows"]
    assert again["hours_rewritten"] == 0
