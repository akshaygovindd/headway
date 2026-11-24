"""Model registry in S3: s3://<raw>/models/forecast/v<N>/ and a latest.json pointer.

A version holds model.txt (the LightGBM booster), lines.json (the categorical levels of `line`),
features.json, window.json (training window), metrics.json (backtest) and meta.json (git sha,
parameters, gate state). latest.json is written last, and only for a model trained under a passed
gate: write_model refuses a line whose gate is not passed.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pandas as pd

PREFIX = "models/forecast"
LATEST = f"{PREFIX}/latest.json"


class GateError(Exception):
    """A model was offered for a line whose training gate is not passed."""


@dataclass
class ModelArtifact:
    model_text: str
    lines: list[str]
    features: list[str]
    window: dict[str, Any]
    metrics: list[dict[str, Any]]
    git_sha: str
    params: dict[str, Any] = field(default_factory=dict)


def next_version(s3: Any, bucket: str) -> int:
    seen = [0]
    for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=bucket, Prefix=f"{PREFIX}/", Delimiter="/"
    ):
        for p in page.get("CommonPrefixes", []):
            m = re.fullmatch(rf"{PREFIX}/v(\d+)/", p["Prefix"])
            if m:
                seen.append(int(m.group(1)))
    return max(seen) + 1


def _put(s3: Any, bucket: str, key: str, body: str) -> None:
    s3.put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType="application/json")


def write_model(s3: Any, bucket: str, art: ModelArtifact, gate: pd.DataFrame) -> int:
    """Write the next version and move latest.json to it. Returns the version number."""
    passed = set(gate.loc[gate["gate_passed"].astype(bool), "line"])
    bad = sorted(set(art.lines) - passed)
    if bad or not art.lines:
        raise GateError(f"training gate not passed for {bad or 'any line'}: no model written")
    version = next_version(s3, bucket)
    base = f"{PREFIX}/v{version}"
    s3.put_object(
        Bucket=bucket, Key=f"{base}/model.txt", Body=art.model_text.encode(),
        ContentType="text/plain",
    )  # fmt: skip
    trained_at = datetime.now(UTC).isoformat()
    for name, body in {
        "lines.json": art.lines,
        "features.json": art.features,
        "window.json": art.window,
        "metrics.json": art.metrics,
        "meta.json": {
            "git_sha": art.git_sha, "params": art.params, "trained_at": trained_at,
            "gate": json.loads(gate.to_json(orient="records")),
        },
    }.items():  # fmt: skip
        _put(s3, bucket, f"{base}/{name}", json.dumps(body, indent=2, default=str))
    _put(
        s3, bucket, LATEST,
        json.dumps({
            "version": version, "path": f"{base}/", "trained_at": trained_at,
            "lines": art.lines, "git_sha": art.git_sha,
        }, indent=2),
    )  # fmt: skip
    return version
