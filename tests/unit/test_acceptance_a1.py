"""A1 reads the last completed minute's manifest, never the run still in progress."""

import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import acceptance  # noqa: E402


def at(minute: int, second: int) -> datetime:
    return datetime(2026, 11, 27, 21, minute, second, tzinfo=UTC)


def test_the_run_in_progress_is_skipped_in_the_first_seconds_of_a_minute() -> None:
    objects = [(at(23, 5), "m/23"), (at(24, 3), "m/24"), (at(24, 40), "m/24b")]
    # 21:24:44: minute 24 is still running, its manifests are not complete
    assert acceptance.last_completed_minute_key(objects, at(24, 44)) == "m/23"
    assert acceptance.last_completed_minute_key(objects, at(24, 5)) == "m/23"


def test_once_the_minute_is_over_its_newest_manifest_is_read() -> None:
    objects = [(at(23, 5), "m/23"), (at(24, 3), "m/24"), (at(24, 40), "m/24b")]
    assert acceptance.last_completed_minute_key(objects, at(25, 0)) == "m/24b"
    assert acceptance.last_completed_minute_key(objects, at(25, 30)) == "m/24b"


def test_nothing_completed_yet_is_none() -> None:
    assert acceptance.last_completed_minute_key([(at(24, 3), "m/24")], at(24, 44)) is None
    assert acceptance.last_completed_minute_key([], at(24, 44)) is None
