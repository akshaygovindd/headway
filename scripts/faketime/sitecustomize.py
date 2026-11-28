"""Pre-push only: put the process clock at FAKE_NOW (epoch seconds) to prove that nothing a build
writes depends on when it runs. Loaded through PYTHONPATH=scripts/faketime."""

import datetime as _dt
import os
import time as _time

_now = os.environ.get("FAKE_NOW")
if _now:
    _fixed = float(_now)
    _time.time = lambda: _fixed

    class _DateTime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[no-untyped-def]
            return cls.fromtimestamp(_fixed, tz)

        @classmethod
        def utcnow(cls):  # type: ignore[no-untyped-def]
            return cls.fromtimestamp(_fixed, _dt.UTC).replace(tzinfo=None)

    _dt.datetime = _DateTime  # type: ignore[misc]
