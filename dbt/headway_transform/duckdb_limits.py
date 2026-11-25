"""Resource limits for every DuckDB connection the transform Lambda opens.

DuckDB sizes itself from the host's RAM, not the Lambda cgroup, so an unconfigured connection
defaults to ~80% of the host and can push the function past its memory cap (INCIDENTS: transform
OutOfMemory at the account's 3008 MB cap). Every connection goes through :func:`connect`.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_MEMORY_LIMIT = "2200MB"
DEFAULT_THREADS = 2
DEFAULT_MAX_TEMP = "8GB"
DEFAULT_SPILL_DIR = "/tmp/duckdb_spill"  # noqa: S108 - Lambda's only writable path


def settings() -> dict[str, Any]:
    """The DuckDB config applied to every connection, from the environment."""
    return {
        "memory_limit": os.environ.get("HEADWAY_DUCKDB_MEMORY_LIMIT", DEFAULT_MEMORY_LIMIT),
        "threads": int(os.environ.get("HEADWAY_DUCKDB_THREADS", DEFAULT_THREADS)),
        "temp_directory": os.environ.get("HEADWAY_DUCKDB_TEMP_DIR", DEFAULT_SPILL_DIR),
        "max_temp_directory_size": os.environ.get("HEADWAY_DUCKDB_MAX_TEMP", DEFAULT_MAX_TEMP),
        "preserve_insertion_order": False,
    }


def connect(
    database: str = ":memory:",
    read_only: bool = False,
    config: dict[str, Any] | None = None,
) -> duckdb.DuckDBPyConnection:
    """``duckdb.connect`` with the memory, thread and spill limits applied."""
    merged = settings()
    merged.update(config or {})
    Path(str(merged["temp_directory"])).mkdir(parents=True, exist_ok=True)
    return duckdb.connect(database, read_only=read_only, config=merged)
