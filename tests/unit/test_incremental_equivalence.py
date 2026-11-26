"""An incremental build (closed days from persisted facts) equals a full rebuild, table for table.

scripts/check_incremental.py builds the whole project twice and compares every table (ADR-024).
"""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
REAL = ROOT / ".local" / "spike_root"  # 27 h of real captures, 2026-11-22 to 23 (gitignored)


def _check(curated_root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    return subprocess.run(
        [
            "uv", "run", "--project", "dbt", "python", "scripts/check_incremental.py",
            "--curated-root", str(curated_root), *extra,
        ],  # fmt: skip
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip


@pytest.fixture(scope="module")
def sample_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("replay")
    env = {k: v for k, v in os.environ.items() if k not in {"VIRTUAL_ENV", "UV_PROJECT"}}
    subprocess.run(
        [
            "uv", "run", "headway", "replay", "--source", "samples/boston",
            "--target", str(target), "--city", "boston",
        ],  # fmt: skip
        cwd=ROOT, env=env, check=True, capture_output=True,
    )  # fmt: skip
    return target


def test_incremental_equals_full_on_the_sample(sample_root: Path) -> None:
    # one service day, persisted: every fact of the incremental build comes from state
    proc = _check(sample_root, "--persist", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "incremental == full" in proc.stdout


@pytest.mark.skipif(not REAL.exists(), reason="the 27 h real capture is not on this machine")
def test_incremental_equals_full_on_27_hours_of_real_data() -> None:
    # service day 22 persisted, 23 computed: trips and arrivals cross the UTC date boundary
    proc = _check(REAL, "--persist", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "persisted ['2026-11-22'], computed ['2026-11-23']" in proc.stdout


@pytest.mark.skipif(not REAL.exists(), reason="the 27 h real capture is not on this machine")
def test_the_comparison_catches_a_tampered_persisted_day() -> None:
    proc = _check(REAL, "--persist", "1", "--tamper")
    assert proc.returncode == 1
    assert "DIFF fct_headways" in proc.stderr
