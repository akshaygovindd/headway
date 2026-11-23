"""Load gold tables and the live snapshot from a data URL (file:// or https://).

The URL comes from the HEADWAY_DATA_URL environment variable, then the Streamlit secret of the
same name, then the public default below. Only public HTTPS objects are read; no credentials.

Layout under the base URL: gold/<table>.parquet, gold/manifest.json,
live/vehicles_latest.json. Caching is left to the callers (the Streamlit pages).
"""

import io
import json
import os
import urllib.error
import urllib.request
from typing import Any

import pandas as pd

TIMEOUT_SECONDS = 15
ENV_VAR = "HEADWAY_DATA_URL"
DEFAULT_URL = "https://headway-public-381232271655.s3.us-east-1.amazonaws.com/public/boston"
GOLD_TABLES = (
    "agg_line_reliability_hourly",
    "agg_line_reliability_window",
    "agg_stop_deviation_daily",
    "agg_pipeline_health",
    "agg_quality_checks_hourly",
    "dim_route",
    "dim_stop",
    "route_shapes",
    "cost_daily",
)


class DataUnavailableError(RuntimeError):
    """The data source could not be read."""


def secret_url() -> str:
    """The HEADWAY_DATA_URL Streamlit secret, or "" when there is no secrets file or key."""
    try:
        import streamlit as st

        return str(st.secrets.get(ENV_VAR, "") or "")
    except Exception:  # no secrets.toml, or streamlit unavailable
        return ""


def base_url(value: str | None = None) -> str:
    """Resolve the data root: `value`, else env, else Streamlit secret, else the public default."""
    url = value if value is not None else os.environ.get(ENV_VAR) or secret_url() or DEFAULT_URL
    if not url.startswith(("file://", "https://")):
        raise DataUnavailableError(f"{ENV_VAR} must start with file:// or https://")
    return url.rstrip("/")


def read_bytes(url: str) -> bytes:
    if not url.startswith(("file://", "https://")):
        raise DataUnavailableError(f"unsupported URL scheme: {url}")
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
            return bytes(resp.read())
    except (urllib.error.URLError, OSError) as exc:
        raise DataUnavailableError(f"cannot read {url}: {exc}") from exc


def read_json(url: str) -> dict[str, Any]:
    try:
        doc = json.loads(read_bytes(url))
    except json.JSONDecodeError as exc:
        raise DataUnavailableError(f"invalid JSON at {url}: {exc}") from exc
    if not isinstance(doc, dict):
        raise DataUnavailableError(f"expected a JSON object at {url}")
    return doc


def load_table(base: str, name: str) -> pd.DataFrame:
    if name not in GOLD_TABLES:
        raise ValueError(f"not a gold table: {name}")
    return pd.read_parquet(io.BytesIO(read_bytes(f"{base}/gold/{name}.parquet")))


def load_manifest(base: str) -> dict[str, Any]:
    return read_json(f"{base}/gold/manifest.json")


def load_snapshot(base: str) -> dict[str, Any]:
    return read_json(f"{base}/live/vehicles_latest.json")
