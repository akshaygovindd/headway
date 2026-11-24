"""Cost and abuse controls of the analyst (ADR-021).

A daily question budget (default 100) and a per-session limit (default 10). The daily count lives
in process and in a small JSON file (date keyed), so a restart of the app does not reset it. Over
budget the page shows the cached examples and calls no model.
"""

import contextlib
import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

ENV_DAILY = "HEADWAY_ANALYST_DAILY_BUDGET"
ENV_SESSION = "HEADWAY_ANALYST_SESSION_LIMIT"
DEFAULT_DAILY = 100
DEFAULT_SESSION = 10


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


def daily_budget() -> int:
    return _int_env(ENV_DAILY, DEFAULT_DAILY)


def session_limit() -> int:
    return _int_env(ENV_SESSION, DEFAULT_SESSION)


def default_path() -> Path:
    return Path(tempfile.gettempdir()) / "headway_analyst_budget.json"


class DailyBudget:
    """Counts questions per UTC day; persisted best-effort (a write error keeps the count)."""

    def __init__(self, path: Path | None = None, limit: int | None = None) -> None:
        self.path = path if path is not None else default_path()
        self.limit = daily_budget() if limit is None else limit
        self._lock = threading.Lock()
        self._day = ""
        self._count = 0

    @staticmethod
    def _today() -> str:
        return datetime.now(UTC).date().isoformat()

    def _load(self) -> int:
        today = self._today()
        persisted = 0
        try:
            doc = json.loads(self.path.read_text())
            if doc.get("date") == today:
                persisted = int(doc.get("count", 0))
        except (OSError, ValueError, AttributeError):
            pass
        if self._day != today:
            self._day, self._count = today, 0
        self._count = max(self._count, persisted)
        return self._count

    def used(self) -> int:
        with self._lock:
            return self._load()

    def try_take(self) -> bool:
        """Count one question; False (and nothing counted) when the day's budget is spent."""
        with self._lock:
            if self._load() >= self.limit:
                return False
            self._count += 1
            with contextlib.suppress(OSError):
                self.path.write_text(json.dumps({"date": self._day, "count": self._count}))
            return True


class SessionLimit:
    """Questions asked by one session; the caller keeps the instance in its session state."""

    def __init__(self, limit: int | None = None) -> None:
        self.limit = session_limit() if limit is None else limit
        self.used = 0

    def try_take(self) -> bool:
        if self.used >= self.limit:
            return False
        self.used += 1
        return True


def admit(daily: DailyBudget, session: SessionLimit) -> str:
    """ "ok", or why not: "session" or "daily". The session is checked first and a refused
    question is never counted."""
    if session.used >= session.limit:
        return "session"
    if not daily.try_take():
        return "daily"
    session.used += 1
    return "ok"
