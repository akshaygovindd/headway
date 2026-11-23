"""Guards on infra/main/scheduler.tf: at-least-once delivery must not be made worse by retries."""

import re
from pathlib import Path

SCHEDULER_TF = Path(__file__).resolve().parents[2] / "infra" / "main" / "scheduler.tf"


def _schedule_blocks() -> list[str]:
    text = SCHEDULER_TF.read_text()
    starts = [m.start() for m in re.finditer(r'^resource "aws_scheduler_schedule"', text, re.M)]
    return [text[a:b] for a, b in zip(starts, [*starts[1:], len(text)], strict=True)]


def test_every_schedule_declares_zero_retries() -> None:
    blocks = _schedule_blocks()
    assert len(blocks) >= 2  # the ingest schedule and the for_each pipeline schedules
    for block in blocks:
        assert re.search(r"retry_policy\s*{\s*maximum_retry_attempts\s*=\s*0\s*}", block), block
