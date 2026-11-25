"""Every DuckDB connection in the transform is bounded (ADR-024: account Lambda cap 3008 MB)."""

import re
from pathlib import Path

import pytest

from headway_transform import duckdb_limits

ROOT = Path(__file__).resolve().parents[2]


def test_defaults_bound_memory_threads_and_spill(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HEADWAY_DUCKDB_MEMORY_LIMIT",
        "HEADWAY_DUCKDB_THREADS",
        "HEADWAY_DUCKDB_TEMP_DIR",
        "HEADWAY_DUCKDB_MAX_TEMP",
    ):
        monkeypatch.delenv(name, raising=False)
    assert duckdb_limits.settings() == {
        "memory_limit": "2200MB",
        "threads": 2,
        "temp_directory": "/tmp/duckdb_spill",
        "max_temp_directory_size": "8GB",
        "preserve_insertion_order": False,
    }


def test_environment_overrides_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_DUCKDB_MEMORY_LIMIT", "1GB")
    monkeypatch.setenv("HEADWAY_DUCKDB_THREADS", "3")
    assert duckdb_limits.settings()["memory_limit"] == "1GB"
    assert duckdb_limits.settings()["threads"] == 3


def test_connect_applies_the_limits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HEADWAY_DUCKDB_MEMORY_LIMIT", "512MB")
    monkeypatch.setenv("HEADWAY_DUCKDB_THREADS", "1")
    monkeypatch.setenv("HEADWAY_DUCKDB_TEMP_DIR", str(tmp_path / "spill"))
    con = duckdb_limits.connect()
    try:
        row = con.execute(
            "select current_setting('threads'), current_setting('preserve_insertion_order'),"
            " current_setting('temp_directory'), current_setting('memory_limit')"
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[0] == 1
    assert row[1] is False
    assert row[2] == str(tmp_path / "spill")
    assert row[3].startswith(("488", "512"))  # DuckDB reports MiB


def test_no_unbounded_connection_in_the_transform_package() -> None:
    for path in (ROOT / "dbt" / "headway_transform").glob("*.py"):
        if path.name == "duckdb_limits.py":
            continue
        assert "duckdb.connect(" not in path.read_text().replace("duckdb_limits.connect(", ""), path


def test_lambda_profile_and_terraform_carry_the_limits() -> None:
    profiles = (ROOT / "dbt" / "headway" / "profiles.yml").read_text()
    lam = profiles.split("    lambda:")[1].split("    s3_test:")[0]
    for key in ("memory_limit", "temp_directory", "max_temp_directory_size"):
        assert key in lam
    assert "preserve_insertion_order: false" in lam
    assert "HEADWAY_DUCKDB_THREADS', '2'" in lam
    tf = (ROOT / "infra" / "main" / "pipeline.tf").read_text()
    block = tf.split('resource "aws_lambda_function" "transform"')[1].split("logging_config")[0]
    assert re.search(r"ephemeral_storage\s*{\s*size\s*=\s*10240", block)
    assert "HEADWAY_DUCKDB_MEMORY_LIMIT" in block


def test_memory_percentage_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    from headway_transform import memory

    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", raising=False)
    monkeypatch.delenv("HEADWAY_MEMORY_LIMIT_MB", raising=False)
    assert memory.limit_mb() == 3008
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "1000")
    assert memory.used_pct(850.0) == 85.0


def test_tracker_reports_spill_per_step(tmp_path: Path) -> None:
    from headway_transform import memory

    tracker = memory.Tracker(str(tmp_path), interval_s=0.01).start()
    (tmp_path / "spill.tmp").write_bytes(b"x" * 3 * 1024 * 1024)
    tracker.mark("a")
    tracker.stop()
    assert tracker.report()["step_spill_mb"] == {"a": 3}
    assert tracker.report()["peak_memory_mb"] > 0
