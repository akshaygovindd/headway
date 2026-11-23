"""Chaos switch (ADR-017): parsing, expiry, failure to off, and the two copies agreeing."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from headway import chaos as ingest_chaos
from headway_transform import chaos as transform_chaos

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
LATER = (NOW + timedelta(minutes=15)).isoformat().replace("+00:00", "Z")
EARLIER = (NOW - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")

CASES = [
    (f'{{"mode": "feed_down", "until": "{LATER}"}}', "feed_down"),
    (f'{{"mode": "md_down", "until": "{LATER}"}}', "md_down"),
    (f'{{"mode": "off", "until": "{LATER}"}}', "off"),
    (f'{{"mode": "feed_down", "until": "{EARLIER}"}}', "off"),  # expired
    (f'{{"mode": "feed_down", "until": "{NOW.isoformat()}"}}', "off"),  # until is exclusive
    ('{"mode": "feed_down"}', "off"),  # no until
    ('{"mode": "feed_down", "until": "tomorrow"}', "off"),
    ('{"mode": "feed_down", "until": "2030-01-01T00:00:00"}', "off"),  # naive timestamp
    (f'{{"mode": "explode", "until": "{LATER}"}}', "off"),
    ("not json", "off"),
    ("[]", "off"),
    ("", "off"),
    (None, "off"),
]


@pytest.mark.parametrize("module", [ingest_chaos, transform_chaos])
@pytest.mark.parametrize(("raw", "mode"), CASES)
def test_parse(module: Any, raw: str | None, mode: str) -> None:
    assert module.parse(raw, NOW).mode == mode


def test_the_two_copies_agree_on_everything() -> None:
    assert ingest_chaos.PARAMETER == transform_chaos.PARAMETER == "/headway/chaos"
    assert ingest_chaos.MODES == transform_chaos.MODES == ("feed_down", "md_down")
    assert ingest_chaos.READ_TIMEOUT_S == transform_chaos.READ_TIMEOUT_S
    for raw, _ in CASES:
        a, b = ingest_chaos.parse(raw, NOW), transform_chaos.parse(raw, NOW)
        assert (a.mode, a.until) == (b.mode, b.until)


@pytest.fixture
def ssm(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        yield boto3.client("ssm")


@pytest.mark.parametrize("module", [ingest_chaos, transform_chaos])
def test_a_missing_parameter_is_off_and_quiet(
    module: Any, ssm: Any, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    assert module.read(NOW).mode == "off"
    assert caplog.records == []


@pytest.mark.parametrize("module", [ingest_chaos, transform_chaos])
def test_an_active_parameter_is_logged_loudly(
    module: Any, ssm: Any, caplog: pytest.LogCaptureFixture
) -> None:
    ssm.put_parameter(
        Name="/headway/chaos", Type="String", Value=f'{{"mode":"md_down","until":"{LATER}"}}'
    )
    caplog.set_level(logging.INFO)
    chaos = module.read(NOW)
    assert chaos.active("md_down") and not chaos.active("feed_down")
    (record,) = [r for r in caplog.records if "CHAOS ACTIVE" in r.message]
    assert "mode=md_down" in record.message and record.levelno == logging.WARNING


@pytest.mark.parametrize("module", [ingest_chaos, transform_chaos])
def test_an_expired_parameter_is_off(module: Any, ssm: Any) -> None:
    ssm.put_parameter(
        Name="/headway/chaos", Type="String", Value=f'{{"mode":"feed_down","until":"{EARLIER}"}}'
    )
    assert module.read(NOW).mode == "off"


@pytest.mark.parametrize("module", [ingest_chaos, transform_chaos])
def test_a_failing_read_is_off_with_a_warning(
    module: Any, caplog: pytest.LogCaptureFixture
) -> None:
    class Broken:
        def get_parameter(self, **_: Any) -> Any:
            raise TimeoutError("ssm timed out")

    caplog.set_level(logging.INFO)
    assert module.read(NOW, client=Broken()).mode == "off"
    assert any("treating as off" in r.message for r in caplog.records)


def test_the_client_has_a_short_timeout_and_a_single_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_client(name: str, config: Any = None, **_: Any) -> Any:
        seen["config"] = config
        raise RuntimeError("stop here")

    monkeypatch.setattr(ingest_chaos.boto3, "client", fake_client)
    assert ingest_chaos.read(NOW).mode == "off"
    cfg = seen["config"]
    assert cfg.connect_timeout == cfg.read_timeout == 2
    assert cfg.retries["max_attempts"] == 1
