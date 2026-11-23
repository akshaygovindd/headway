"""HTTP fetching of realtime feeds with bounded retries."""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import httpx

TIMEOUT_SECONDS = 10.0
BACKOFFS_SECONDS = (1.0, 3.0)


@dataclass(frozen=True)
class FetchResult:
    status: Literal["ok", "error"]
    http_status: int | None
    body: bytes | None
    fetched_at: datetime
    error: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def fetch_feed(
    url: str,
    client: httpx.Client,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = _utcnow,
    backoffs: tuple[float, ...] = BACKOFFS_SECONDS,
    timeout: float = TIMEOUT_SECONDS,
) -> FetchResult:
    """GET url, retrying transport errors and 5xx responses; 4xx is not retried.

    ``backoffs`` is the sleep before each retry (empty: a single attempt).
    """
    http_status: int | None = None
    error = "no attempt made"
    for attempt in range(len(backoffs) + 1):
        if attempt > 0:
            sleep(backoffs[attempt - 1])
        try:
            response = client.get(url, headers={"Accept-Encoding": "gzip"}, timeout=timeout)
        except httpx.HTTPError as exc:
            http_status = None
            error = f"{type(exc).__name__}: {exc}"
            continue
        http_status = response.status_code
        if response.status_code == 200:
            return FetchResult("ok", http_status, response.content, now(), None)
        error = f"HTTP {response.status_code}"
        if response.status_code < 500:
            break
    return FetchResult("error", http_status, None, now(), error)
