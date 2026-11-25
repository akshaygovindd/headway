"""Memory and spill accounting for the transform Lambda (ADR-024).

The account caps Lambda memory at 3008 MB, so the run reports its own high-water marks: the
process peak (``ru_maxrss``), the cgroup peak when readable, and the DuckDB spill directory size,
per step. The peak as a percentage of the function's limit is an EMF metric with an alarm.
"""

from __future__ import annotations

import os
import resource
import sys
import threading
from pathlib import Path

DEFAULT_LIMIT_MB = 3008
ALARM_PCT = 85.0
_CGROUP_PEAKS = ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes")


def limit_mb() -> int:
    """The function's configured memory (set by the Lambda runtime), else the account cap."""
    raw = os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE") or os.environ.get(
        "HEADWAY_MEMORY_LIMIT_MB"
    )
    return int(raw) if raw else DEFAULT_LIMIT_MB


def rss_peak_mb() -> float:
    """High-water resident set of this process so far, in MB."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def cgroup_peak_mb() -> float | None:
    for name in _CGROUP_PEAKS:
        try:
            return int(Path(name).read_text().strip()) / (1024 * 1024)
        except (OSError, ValueError):
            continue
    return None


def peak_mb() -> float:
    """The larger of the process and cgroup high-water marks."""
    cgroup = cgroup_peak_mb()
    return max(rss_peak_mb(), cgroup or 0.0)


def used_pct(peak: float | None = None, limit: int | None = None) -> float:
    return round(100 * (peak_mb() if peak is None else peak) / (limit or limit_mb()), 1)


def dir_size_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total / (1024 * 1024)


class Tracker:
    """Per-step peaks: ``mark(step)`` closes a step with its peak and the spill seen during it.

    A background thread samples the spill directory, because DuckDB removes spill files as soon
    as a query ends and a sample at the step boundary would always read zero.
    """

    def __init__(self, spill_dir: str, interval_s: float = 0.5) -> None:
        self.spill_dir = spill_dir
        self.interval_s = interval_s
        self.step_peak_mb: dict[str, float] = {}
        self.step_spill_mb: dict[str, float] = {}
        self._spill_max = 0.0
        self._spill_total_max = 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._sample, daemon=True)

    def start(self) -> Tracker:
        self._thread.start()
        return self

    def _sample(self) -> None:
        while not self._stop.wait(self.interval_s):
            size = dir_size_mb(self.spill_dir)
            self._spill_max = max(self._spill_max, size)
            self._spill_total_max = max(self._spill_total_max, size)

    def mark(self, step: str) -> None:
        self.step_peak_mb[step] = round(peak_mb())
        self.step_spill_mb[step] = round(max(self._spill_max, dir_size_mb(self.spill_dir)))
        self._spill_max = 0.0

    def stop(self) -> None:
        self._stop.set()

    def report(self) -> dict[str, object]:
        peak = peak_mb()
        return {
            "peak_memory_mb": round(peak),
            "memory_limit_mb": limit_mb(),
            "memory_used_pct": used_pct(peak),
            "spill_peak_mb": round(self._spill_total_max),
            "step_peak_mb": self.step_peak_mb,
            "step_spill_mb": self.step_spill_mb,
        }
