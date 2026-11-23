import gzip
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
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
    """Fetcher whose results are stamped with the (fake) clock; vehicle bodies differ per poll."""
    urls = {
        str(CONFIG.feeds.trip_updates): _ok(fx.trip_updates_feed()),
        str(CONFIG.feeds.vehicle_positions): vp or _ok(fx.vehicles_feed()),
        str(CONFIG.feeds.alerts): _ok(fx.alerts_feed()),
    }
    calls = {"vp": 0}

    def fetch(url: str) -> FetchResult:
        result = urls[url]
        if url == str(CONFIG.feeds.vehicle_positions) and result.body is not None:
            calls["vp"] += 1
            result = replace(result, body=fx.vehicles_feed(lat=42.35 + calls["vp"] / 1000))
        return replace(result, fetched_at=handler._now())

    return fetch


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("RAW_BUCKET", "raw-bucket")
    monkeypatch.setenv("PUBLIC_BUCKET", "public-bucket")
    monkeypatch.setenv("CITY_ID", "boston")
    monkeypatch.setenv("VEHICLE_POLLS", "4")
    clock = {"now": NOW}
    monkeypatch.setattr(handler, "_now", lambda: clock["now"])
    # a sleep advances the fake clock, so the 60 s minute runs instantly
    monkeypatch.setattr(
        handler, "_sleep", lambda s: clock.update(now=clock["now"] + timedelta(seconds=s))
    )
    monkeypatch.setattr(handler, "_poll_fetch", lambda url: handler._fetch(url))
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
    assert len(raw) == 6  # three feeds at poll 0, then three vehicle polls
    manifests = _keys(aws, "raw-bucket", "manifests/boston/")
    assert len(manifests) == 1
    assert "dt=2026-06-01/hour=12" in manifests[0]
    manifest = json.loads(aws.get_object(Bucket="raw-bucket", Key=manifests[0])["Body"].read())
    assert [e["status"] for e in manifest["entries"]] == ["ok"] * 6
    assert [e["poll_index"] for e in manifest["entries"]] == [0, 0, 0, 1, 2, 3]
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


def test_scheduled_at_parses_iso_with_z() -> None:
    parsed = handler._scheduled_at({"scheduled_at": "2026-06-01T12:03:00Z"}, NOW)
    assert parsed == datetime(2026, 6, 1, 12, 3, tzinfo=UTC)


def test_scheduled_at_literal_placeholder_falls_back(
    caplog: pytest.LogCaptureFixture,
) -> None:
    event = {"scheduled_at": "<aws.scheduler.scheduled-time>"}
    with caplog.at_level("WARNING"):
        parsed = handler._scheduled_at(event, NOW)
    assert parsed == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert "scheduled_at" in caplog.text


def test_scheduled_at_missing_key_falls_back(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        parsed = handler._scheduled_at({}, NOW)
    assert parsed == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    assert "scheduled_at" in caplog.text


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


def test_second_delivery_of_a_minute_is_recorded_without_fetching(
    aws: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    event = {"scheduled_at": "2026-06-01T12:00:00Z"}
    first = handler.lambda_handler(event, None)
    raw_before = _keys(aws, "raw-bucket", "raw/boston/")

    def must_not_fetch(url: str) -> FetchResult:
        raise AssertionError("a losing delivery must not fetch")

    monkeypatch.setattr(handler, "_fetch", must_not_fetch)
    monkeypatch.setattr(handler, "_poll_fetch", must_not_fetch)
    with caplog.at_level("WARNING"):
        second = handler.lambda_handler(event, None)

    assert "duplicate_delivery" not in first
    assert second["duplicate_delivery"] is True
    assert "duplicate delivery" in caplog.text
    assert _keys(aws, "raw-bucket", "raw/boston/") == raw_before
    manifests = [
        k for k in _keys(aws, "raw-bucket", "manifests/boston/") if "/duplicates/" not in k
    ]
    records = _keys(aws, "raw-bucket", "manifests/boston/duplicates/")
    assert len(manifests) == 1 and len(records) == 1
    assert second["duplicate_record"] == records[0]
    assert second["duplicate_of"] == manifests[0]


def test_vehicle_polls_env_selects_one_or_four_polls(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    monkeypatch.delenv("VEHICLE_POLLS", raising=False)
    one = handler.lambda_handler({"scheduled_at": "2026-06-01T12:00:00Z"}, None)
    assert one["vehicle_polls"] == ["ok"] and len(_keys(aws, "raw-bucket", "raw/boston/")) == 3

    monkeypatch.setenv("VEHICLE_POLLS", "4")
    four = handler.lambda_handler({"scheduled_at": "2026-06-01T12:01:00Z"}, None)
    assert four["vehicle_polls"] == ["ok"] * 4
    manifest = [k for k in _keys(aws, "raw-bucket", "manifests/boston/") if "T1201Z" in k][0]
    entries = json.loads(aws.get_object(Bucket="raw-bucket", Key=manifest)["Body"].read())[
        "entries"
    ]
    assert [e["poll_index"] for e in entries] == [0, 0, 0, 1, 2, 3]

    monkeypatch.setenv("VEHICLE_POLLS", "nonsense")
    bad = handler.lambda_handler({"scheduled_at": "2026-06-01T12:02:00Z"}, None)
    assert bad["vehicle_polls"] == ["ok"]


def test_live_snapshot_is_refreshed_after_every_vehicle_poll(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    writes: list[str] = []
    real = handler.S3Storage.put_bytes

    def spy(self: Any, key: str, data: bytes, **kw: Any) -> None:
        if key.endswith("vehicles_latest.json"):
            writes.append(json.loads(data)["vehicles"][0]["lat"])
        real(self, key, data, **kw)

    monkeypatch.setattr(handler.S3Storage, "put_bytes", spy)
    result = handler.lambda_handler({"scheduled_at": "2026-06-01T12:00:00Z"}, None)
    assert len(writes) == 4 and len(set(writes)) == 4
    assert result["vehicle_polls"] == ["ok"] * 4 and result["skipped_polls"] == []


def test_failed_snapshot_write_still_leaves_manifest_and_all_captures(
    aws: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handler, "_fetch", _fake_fetch())
    monkeypatch.setenv("PUBLIC_BUCKET", "no-such-bucket")
    with pytest.raises(ClientError):
        handler.lambda_handler({"scheduled_at": "2026-06-01T12:00:00Z"}, None)
    assert len(_keys(aws, "raw-bucket", "manifests/boston/")) == 1
    assert len(_keys(aws, "raw-bucket", "raw/boston/")) == 6
