"""Limits and the data root of the analyst tools (ADR-020, ADR-021)."""

import os

ENV_DATA_URL = "HEADWAY_DATA_URL"
DEFAULT_URL = "https://headway-public-381232271655.s3.us-east-1.amazonaws.com/public/boston"
MAX_RANGE_DAYS = 31  # get_delay_trends
MAX_SHORT_RANGE_DAYS = 7  # get_service_alerts, get_pipeline_health
MAX_ROWS = 500
QUERY_TIMEOUT_S = 5.0
HTTP_TIMEOUT_S = 5.0
MIN_COVERAGE_PCT = 50.0  # below it a value is withheld, as on every page


def base_url(value: str | None = None) -> str:
    """The data root: `value`, else HEADWAY_DATA_URL, else the public bucket. file:// or https://."""
    url = value if value is not None else os.environ.get(ENV_DATA_URL) or DEFAULT_URL
    if not url.startswith(("file://", "https://")):
        raise ValueError(f"{ENV_DATA_URL} must start with file:// or https://")
    return url.rstrip("/")
