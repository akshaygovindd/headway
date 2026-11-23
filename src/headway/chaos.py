"""Chaos switch (ADR-017): an SSM parameter that makes the pipeline fail on purpose, for drills.

``/headway/chaos`` holds ``{"mode": "feed_down" | "md_down" | "off", "until": "<UTC ISO>"}``. A mode
is active only before ``until``; a missing parameter, malformed JSON, an unknown mode, a missing or
past ``until`` and any error reading it all mean ``off``. The parameter is read once per
invocation with a short timeout and one attempt, so a slow SSM never costs a run.

Ingest uses ``feed_down`` (every feed is recorded as an error without fetching); the transform
(`headway_transform/chaos.py`, the same rules) uses ``md_down``. This copy serves the ingest zip,
which does not ship the transform package.
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.config import Config

logger = logging.getLogger()

PARAMETER = "/headway/chaos"
MODES = ("feed_down", "md_down")
READ_TIMEOUT_S = 2


@dataclass(frozen=True)
class Chaos:
    mode: str = "off"
    until: datetime | None = None

    def active(self, mode: str) -> bool:
        return self.mode == mode


OFF = Chaos()


def parse(raw: str | None, now: datetime) -> Chaos:
    """The chaos setting a parameter value describes at ``now``; ``OFF`` for anything else."""
    if not raw:
        return OFF
    try:
        doc = json.loads(raw)
        mode = doc["mode"]
        until = datetime.fromisoformat(str(doc["until"]).replace("Z", "+00:00"))
    except (ValueError, KeyError, TypeError):
        return OFF
    if mode not in MODES or until.tzinfo is None:
        return OFF
    until = until.astimezone(UTC)
    return Chaos(mode, until) if now < until else OFF


def read(now: datetime, client: Any = None) -> Chaos:
    """Read the switch once. Never raises: a failure is logged and means ``off``."""
    try:
        ssm = client or boto3.client(
            "ssm",
            config=Config(
                connect_timeout=READ_TIMEOUT_S,
                read_timeout=READ_TIMEOUT_S,
                retries={"max_attempts": 1},
            ),
        )
        raw = ssm.get_parameter(Name=PARAMETER)["Parameter"]["Value"]
    except Exception as exc:
        if type(exc).__name__ != "ParameterNotFound":
            logger.warning(
                "chaos switch unreadable, treating as off: %s: %s", type(exc).__name__, exc
            )
        return OFF
    chaos = parse(raw, now)
    if chaos.mode != "off":
        logger.warning("CHAOS ACTIVE mode=%s until=%s", chaos.mode, chaos.until)
    return chaos
