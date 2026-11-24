"""CloudWatch Embedded Metric Format lines: custom metrics from a log line, no PutMetricData.

CloudWatch Logs turns a stdout line holding an ``_aws`` block into metrics, so the function needs
no extra IAM permission. The line must be bare JSON: it goes through ``print``, not the logger,
whose Lambda handler prefixes a timestamp and request id.
"""

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

NAMESPACE = "Headway"


def emf_line(
    timestamp: datetime,
    metrics: dict[str, float],
    *,
    dimensions: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    namespace: str = NAMESPACE,
    properties: dict[str, Any] | None = None,
) -> str:
    """One EMF document: ``metrics`` are top-level values, ``dimensions`` are string members."""
    dims = dimensions or {}
    unit = units or {}
    doc: dict[str, Any] = {
        "_aws": {
            "Timestamp": int(timestamp.timestamp() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dims)],
                    "Metrics": [
                        {"Name": name, "Unit": unit.get(name, "Count")} for name in metrics
                    ],
                }
            ],
        },
        **dims,
        **(properties or {}),
        **metrics,
    }
    return json.dumps(doc, separators=(",", ":"), sort_keys=False)


def emit(lines: Sequence[str]) -> None:
    for line in lines:
        print(line, flush=True)  # noqa: T201


def run_metrics(
    timestamp: datetime, feed_errors: dict[str, int], vehicle_polls_completed: int
) -> list[str]:
    """``FeedErrorCount`` per feed (dimension ``Feed``) and ``VehiclePollsCompleted`` for a run."""
    lines = [
        emf_line(timestamp, {"FeedErrorCount": count}, dimensions={"Feed": feed})
        for feed, count in feed_errors.items()
    ]
    lines.append(emf_line(timestamp, {"VehiclePollsCompleted": vehicle_polls_completed}))
    return lines
