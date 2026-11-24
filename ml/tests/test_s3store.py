from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import mlflow
import pytest
from headway_ml import s3store, tracking
from moto import mock_aws
from synth import make_examples, make_gate

BUCKET = "raw"


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.delenv("HEADWAY_MLFLOW_URI", raising=False)
    monkeypatch.delenv("HEADWAY_MLFLOW_ARTIFACT_ROOT", raising=False)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _log(db: Path, artifacts: str) -> str:
    from headway_ml import backtest

    bt = backtest.run_backtest(make_examples(weeks=2), make_gate(), reps=10)
    return tracking.log_run(
        f"sqlite:///{db}", {"k": "v"}, {"git_sha": "abc"}, ["f"], bt.results, artifacts=artifacts
    )


def test_database_round_trips_through_s3(s3: Any, tmp_path: Path) -> None:
    with s3store.mlflow_db(s3, BUCKET, tmp_path / "a") as db:
        run_id = _log(db, (tmp_path / "art").as_uri())
    assert not list(tmp_path.glob("a/*.lock"))
    # a fresh /tmp (a new container) sees the run
    with s3store.mlflow_db(s3, BUCKET, tmp_path / "b") as db:
        mlflow.set_tracking_uri(f"sqlite:///{db}")
        assert mlflow.get_run(run_id).data.tags["git_sha"] == "abc"
    with pytest.raises(Exception):  # noqa: B017  the lock was released, the object is gone
        s3.head_object(Bucket=BUCKET, Key=s3store.LOCK_KEY)


def test_a_held_lock_blocks_and_a_stale_one_is_replaced(s3: Any, tmp_path: Path) -> None:
    first = s3store.Lock(s3, BUCKET)
    assert first.acquire()
    with pytest.raises(s3store.Locked), s3store.mlflow_db(s3, BUCKET, tmp_path):
        pass
    later = datetime.now(UTC) + s3store.LOCK_STALE_AFTER + timedelta(minutes=1)
    assert s3store.Lock(s3, BUCKET, now=lambda: later).acquire()


def test_the_database_is_uploaded_even_when_the_run_fails(s3: Any, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError), s3store.mlflow_db(s3, BUCKET, tmp_path) as db:
        _log(db, (tmp_path / "art").as_uri())
        raise RuntimeError("boom")
    s3.head_object(Bucket=BUCKET, Key=s3store.DB_KEY)
