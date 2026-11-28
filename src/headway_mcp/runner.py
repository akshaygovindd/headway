"""Run one fixed SELECT over public Parquet with DuckDB: row limit and a hard timeout.

The SQL is built by `tools` from validated enums and dates only; nothing a caller or a model
types reaches the query text. Reads use `read_parquet` on the data root (https:// or file://).
"""

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any

import duckdb

from headway_mcp import config


class ToolTimeoutError(RuntimeError):
    """The query ran longer than the timeout."""


class DataError(RuntimeError):
    """The data root could not be read."""


ENV_EXTENSIONS_DIR = "HEADWAY_DUCKDB_EXTENSIONS_DIR"  # baked extensions (read-only images)
ENV_HOME = "HEADWAY_DUCKDB_HOME"


def _connect() -> duckdb.DuckDBPyConnection:
    """A DuckDB connection; on a read-only image the baked extension directory is loaded, never
    installed into, and nothing is auto-downloaded."""
    ext, home = os.environ.get(ENV_EXTENSIONS_DIR), os.environ.get(ENV_HOME)
    # UTC, so a timestamptz cast to text reads the same on every host (it was the host's zone)
    cfg: dict[str, Any] = {"TimeZone": "UTC"}
    if ext:
        cfg.update(extension_directory=ext, autoinstall_known_extensions=False,
                   autoload_known_extensions=False)  # fmt: skip
    if home:
        cfg["home_directory"] = home
    return duckdb.connect(config=cfg)


def path_of(base: str, rel: str) -> str:
    """The location DuckDB reads: a plain path for file://, the URL for https://."""
    return f"{base[len('file://') :]}/{rel}" if base.startswith("file://") else f"{base}/{rel}"


def read_json(base: str, rel: str) -> dict[str, Any]:
    url = f"{base}/{rel}"
    try:
        with urllib.request.urlopen(url, timeout=config.HTTP_TIMEOUT_S) as resp:  # noqa: S310
            doc = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DataError(f"cannot read {rel}: {type(exc).__name__}") from exc
    if not isinstance(doc, dict):
        raise DataError(f"{rel} is not a JSON object")
    return doc


def exists(base: str, rel: str) -> bool:
    """Whether the object exists (a HEAD request, or a file check)."""
    if base.startswith("file://"):
        return os.path.exists(path_of(base, rel))
    req = urllib.request.Request(f"{base}/{rel}", method="HEAD")  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=config.HTTP_TIMEOUT_S):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError):
        return False


def run(
    base: str, sql: str, timeout_s: float | None = None
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Execute `sql`; returns column names and rows. Raises ToolTimeoutError after the timeout."""
    limit = config.QUERY_TIMEOUT_S if timeout_s is None else timeout_s
    con = _connect()
    timer = threading.Timer(limit, con.interrupt)
    timer.start()
    try:
        if base.startswith("https://"):
            if not os.environ.get(ENV_EXTENSIONS_DIR):
                con.execute("install httpfs")  # not baked into the image: download it
            con.execute("load httpfs")
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            return cols, cur.fetchall()
        except duckdb.InterruptException as exc:
            raise ToolTimeoutError(f"query exceeded {limit:g} s") from exc
        except duckdb.Error as exc:
            raise DataError(f"query failed: {type(exc).__name__}") from exc
    finally:
        timer.cancel()
        con.close()
