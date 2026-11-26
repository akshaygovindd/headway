"""The curated window is fetched in parallel to local disk, with the staging models' own window."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from headway_transform import curated_cache, handler

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)  # lookback 7: dt >= 2026-11-19


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in {
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ACCESS_KEY_ID": "t",
        "AWS_SECRET_ACCESS_KEY": "t",
        "RAW_BUCKET": "raw",
        "CITY_ID": "boston",
    }.items():
        monkeypatch.setenv(k, v)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="raw")
        yield client


def _put(s3: Any, *keys: str) -> None:
    for key in keys:
        s3.put_object(Bucket="raw", Key=key, Body=key.encode())


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


def test_only_the_window_and_the_static_tables_are_fetched(s3: Any, tmp_path: Path) -> None:
    window = [
        "curated/boston/table=trip_updates/dt=2026-11-19/hour=00/part-compacted.parquet",
        "curated/boston/table=trip_updates/dt=2026-11-26/hour=11/part-1.parquet",
        "curated/boston/table=trip_updates/dt=2026-11-26/hour=11/part-2.parquet",
        "curated/boston/table=vehicle_positions/dt=2026-11-25/hour=03/part-compacted.parquet",
        "curated/boston/table=manifests/dt=2026-11-26/hour=10/part-compacted.parquet",
        "curated/boston/static/table=stops/feed_version=v1/part.parquet",
        "curated/boston/static/table=trips/feed_version=v1/part.parquet",
    ]
    outside = [
        "curated/boston/table=trip_updates/dt=2026-11-18/hour=23/part-compacted.parquet",
        "curated/boston/table=vehicle_positions/dt=2026-10-01/hour=00/part-compacted.parquet",
        "curated/other/table=trip_updates/dt=2026-11-26/hour=00/part.parquet",
        "state/transform_runs/dt=2026-11-26/rows.parquet",
    ]
    _put(s3, *window, *outside)
    out = curated_cache.prefetch(s3, "raw", "boston", NOW, 7, tmp_path)
    assert _files(tmp_path) == sorted(window)  # laid out as in the bucket
    assert (tmp_path / window[1]).read_bytes() == window[1].encode()
    assert out["objects"] == len(window) and out["bytes"] == sum(len(k) for k in window)
    assert out["vanished"] == 0 and out["root"] == str(tmp_path)


def test_a_part_file_compacted_away_after_the_listing_is_replaced_by_the_compacted_one(
    s3: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hour = "curated/boston/table=trip_updates/dt=2026-11-26/hour=10/"
    _put(s3, hour + "part-a.parquet", hour + "part-b.parquet")
    real_get = curated_cache._get
    done: list[str] = []

    def compact_then_get(client: Any, bucket: str, key: str, path: Path) -> int | None:
        if not done:  # the first GET finds the hour already compacted
            done.append(key)
            client.delete_object(Bucket=bucket, Key=hour + "part-a.parquet")
            client.delete_object(Bucket=bucket, Key=hour + "part-b.parquet")
            client.put_object(Bucket=bucket, Key=hour + "part-compacted.parquet", Body=b"all")
        return real_get(client, bucket, key, path)

    monkeypatch.setattr(curated_cache, "_get", compact_then_get)
    monkeypatch.setattr(curated_cache, "WORKERS", 1)
    out = curated_cache.prefetch(s3, "raw", "boston", NOW, 7, tmp_path)
    assert (tmp_path / hour / "part-compacted.parquet").read_bytes() == b"all"
    assert out["vanished"] >= 1 and out["retried"] == 1


def test_a_stale_cache_from_a_warm_container_is_cleared(s3: Any, tmp_path: Path) -> None:
    (tmp_path / "curated/boston/table=trip_updates/dt=2026-11-01").mkdir(parents=True)
    (tmp_path / "curated/boston/table=trip_updates/dt=2026-11-01/old.parquet").write_text("x")
    _put(s3, "curated/boston/table=alerts/dt=2026-11-26/hour=01/part.parquet")
    curated_cache.prefetch(s3, "raw", "boston", NOW, 7, tmp_path)
    assert _files(tmp_path) == ["curated/boston/table=alerts/dt=2026-11-26/hour=01/part.parquet"]


def test_the_handler_reads_s3_when_the_prefetch_fails_or_is_off(
    s3: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HEADWAY_CURATED_CACHE", str(tmp_path / "c"))
    assert handler._prefetch(s3, 7, NOW) is not None
    monkeypatch.setenv("RAW_BUCKET", "no-such-bucket")
    assert handler._prefetch(s3, 7, NOW) is None  # a failure costs time, never the run
    monkeypatch.setenv("HEADWAY_PREFETCH_CURATED", "false")
    assert handler._prefetch(s3, 7, NOW) is None
    import json

    assert json.loads(handler._variables(7, NOW))["curated_root"] == "s3://no-such-bucket"
