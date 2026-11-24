"""Serving: the registered model scoring the features of the next hour (ADR-019).

The transform Lambda calls `load_latest` once per invocation and scores with the same
`model.matrix` the training used, so a feature is encoded in one place. The model files are
downloaded to /tmp once per cold start and kept by version: a warm container reads only the small
`latest.json`. No `latest.json` means no approved model: the caller serves baselines only.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from headway_ml import model, registry

CACHE_DIR = Path(os.environ.get("HEADWAY_FORECAST_MODEL_DIR", "/tmp/forecast_model"))  # noqa: S108
_LOADED: dict[int, "ServingModel"] = {}


@dataclass
class ServingModel:
    version: int
    lines: list[str]
    features: list[str]
    trained_at: str
    booster: Any
    from_cache: bool = False

    def score(self, rows: pd.DataFrame) -> np.ndarray:
        """Forecast delay in seconds for each row; only rows of lines the model was trained on."""
        unknown = sorted(set(rows["line"]) - set(self.lines))
        if unknown:
            raise ValueError(f"model v{self.version} was not trained on {unknown}")
        return np.asarray(self.booster.predict(model.matrix(prepare(rows), self.lines)), "float64")


def prepare(rows: pd.DataFrame) -> pd.DataFrame:
    """The frame as training saw it (data.load_examples): no missing alert flag."""
    out = rows.copy()
    out["active_disruption_alert"] = out["active_disruption_alert"].fillna(False).astype(bool)
    return out


def _get_json(s3: Any, bucket: str, key: str) -> Any:
    return json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())


def load_latest(s3: Any, bucket: str, cache_dir: Path | None = None) -> ServingModel | None:
    """The model `latest.json` points at, or None when there is none."""
    import lightgbm as lgb  # lazy: needs libgomp (the images install it)

    try:
        latest = _get_json(s3, bucket, registry.LATEST)
    except s3.exceptions.NoSuchKey:
        return None
    version = int(latest["version"])
    if version in _LOADED:
        return _LOADED[version]
    base = (cache_dir or CACHE_DIR) / f"v{version}"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "model.txt"
    cached = path.exists() and (base / "meta.json").exists()
    if not cached:
        prefix = f"{registry.PREFIX}/v{version}"
        tmp = base / "model.txt.part"
        s3.download_file(bucket, f"{prefix}/model.txt", str(tmp))
        (base / "lines.json").write_text(json.dumps(_get_json(s3, bucket, f"{prefix}/lines.json")))
        (base / "features.json").write_text(
            json.dumps(_get_json(s3, bucket, f"{prefix}/features.json"))
        )
        (base / "meta.json").write_text(json.dumps(latest))
        tmp.replace(path)
    loaded = ServingModel(
        version=version,
        lines=json.loads((base / "lines.json").read_text()),
        features=json.loads((base / "features.json").read_text()),
        trained_at=str(latest.get("trained_at", "")),
        booster=lgb.Booster(model_file=str(path)),
        from_cache=cached,
    )
    if loaded.features != model.feature_names():
        raise ValueError(f"model v{version} was trained on other features: {loaded.features}")
    _LOADED[version] = loaded
    return loaded


def reset() -> None:
    """Forget the in-memory models (tests; a cold start has none)."""
    _LOADED.clear()
