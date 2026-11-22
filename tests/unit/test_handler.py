import gzip
import json
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from fixtures import gtfs_rt as fx
from headway.config import load_city_config
from headway.ingest import handler
from headway.ingest.fetch import FetchResult

NOW = datetime(2026, 6, 1, 12, 0, 7, tzinfo=UTC)
CONFIG = load_city_config("boston")


def _ok(body: bytes) -> FetchResult:
    return FetchResult("ok", 200, body, NOW, None)


def _fake_fetch(vp: FetchResult | None = None) -> Any:
    urls = {
        str(CONFIG.feeds.trip_updates): _ok(fx.trip_updates_feed()),
        str(CONFIG.feeds.vehicle_positions): vp or _ok(fx.vehicles_feed()),
        str(CONFIG.feeds.alerts): _ok(fx.alerts_feed()),
    }
    return lambda url: urls[url]


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("RAW_BUCKET", "raw-bucket")
    monkeypatch.setenv("PUBLIC_BUCKET", "public-bucket")
    monkeypatch.setenv("CITY_ID", "boston")
    monkeypatch.setattr(handler, "_now", lambda: NOW)
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="public-bucket")
        yield s3


def _keys(s3: Any, bucket: str, prefix: str) -> list[str]:
    resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    return [o["Key"] for o in resp.get("Contents", [])]


def test_end_to_end_writes_raw_manifest_and_live_snapshot(
    aws: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    result = handler.lambda_handler({"scheduled_at": "2026-06-01T12:00:00Z"}, None)

    raw = _keys(aws, "raw-bucket", "raw/boston/")
    assert len(raw) == 3
    manifests = _keys(aws, "raw-bucket", "manifests/boston/")
    assert len(manifests) == 1
    assert "dt=2026-06-01/hour=12" in manifests[0]
    manifest = json.loads(aws.get_object(Bucket="raw-bucket", Key=manifests[0])["Body"].read())
    assert [e["status"] for e in manifest["entries"]] == ["ok", "ok", "ok"]
    assert gzip.decompress(aws.get_object(Bucket="raw-bucket", Key=raw[0])["Body"].read())

    obj = aws.get_object(Bucket="public-bucket", Key="public/boston/live/vehicles_latest.json")
    assert obj["ContentType"] == "application/json"
    assert obj["CacheControl"] == "max-age=30"
    snap = json.loads(obj["Body"].read())
    assert snap["vehicle_count"] == 1
    assert snap["vehicles"][0]["route_id"] == "Red"

    assert result["vehicle_count"] == 1
    assert result["statuses"] == {
        "trip_updates": "ok",
        "vehicle_positions": "ok",
        "alerts": "ok",
    }
    assert result["run_id"] == manifest["run_id"]
    assert result["scheduled_at"] == "2026-06-01T12:00:00+00:00"


def test_scheduled_at_defaults_to_now_floored_to_minute(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    result = handler.lambda_handler({}, None)
    assert result["scheduled_at"] == "2026-06-01T12:00:00+00:00"


def test_feed_failure_does_not_raise_and_skips_snapshot(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = FetchResult("error", 503, None, NOW, "HTTP 503")
    monkeypatch.setattr(handler, "_fetch", _fake_fetch(failed))
    result = handler.lambda_handler({}, None)
    assert result["statuses"]["vehicle_positions"] == "error"
    assert result["vehicle_count"] is None
    assert _keys(aws, "public-bucket", "public/") == []
    assert len(_keys(aws, "raw-bucket", "manifests/")) == 1


def test_s3_write_failure_raises(aws: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    monkeypatch.setenv("PUBLIC_BUCKET", "no-such-bucket")
    with pytest.raises(ClientError):
        handler.lambda_handler({}, None)
