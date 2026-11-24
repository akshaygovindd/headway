"""The MLflow database in S3: one writer, a conditional-put lock, download at start, upload at end.

The training Lambda works on /tmp/mlflow/mlflow.db. s3://<raw>/mlflow/mlflow.db is the durable copy.
The lock (mlflow/train.lock) is the transform's pattern: created with IfNoneMatch, a lock older
than LOCK_STALE_AFTER (a run that crashed or timed out) is replaced with IfMatch on its ETag.
"""

import json
import logging
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DB_KEY = "mlflow/mlflow.db"
LOCK_KEY = "mlflow/train.lock"
LOCK_STALE_AFTER = timedelta(minutes=20)  # the Lambda limit is 15 minutes
_CONFLICT = {"PreconditionFailed", "ConditionalRequestConflict"}


def _now() -> datetime:
    return datetime.now(UTC)


class Lock:
    def __init__(
        self, s3: Any, bucket: str, key: str = LOCK_KEY, now: Callable[[], datetime] = _now
    ) -> None:
        self.s3, self.bucket, self.key, self.now = s3, bucket, key, now
        self.run_id = uuid.uuid4().hex

    def _put(self, **condition: str) -> bool:
        body = json.dumps({"run_id": self.run_id, "started_at": self.now().isoformat()}).encode()
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self.key, Body=body, **condition)
        except self.s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in _CONFLICT | {"NoSuchKey"}:
                return False
            raise
        return True

    def acquire(self) -> bool:
        for _ in range(2):
            if self._put(IfNoneMatch="*"):
                return True
            try:
                held = self.s3.get_object(Bucket=self.bucket, Key=self.key)
            except self.s3.exceptions.NoSuchKey:
                continue
            try:
                started = datetime.fromisoformat(json.loads(held["Body"].read())["started_at"])
            except (ValueError, KeyError, TypeError):
                started = held["LastModified"]
            if self.now() - started <= LOCK_STALE_AFTER:
                return False
            logger.warning("replacing stale training lock from %s", started.isoformat())
            return self._put(IfMatch=held["ETag"])
        return False

    def release(self) -> None:
        try:
            held = json.loads(self.s3.get_object(Bucket=self.bucket, Key=self.key)["Body"].read())
            if held.get("run_id") == self.run_id:
                self.s3.delete_object(Bucket=self.bucket, Key=self.key)
        except Exception:
            logger.exception("could not release the training lock")


class Locked(Exception):
    """Another training run holds the lock."""


@contextmanager
def mlflow_db(s3: Any, bucket: str, workdir: Path) -> Iterator[Path]:
    """Hold the lock, download the database (if any) into workdir, yield its path, upload it back
    (also when the body raised, so a half-finished run stays visible), release the lock."""
    lock = Lock(s3, bucket)
    if not lock.acquire():
        raise Locked(f"s3://{bucket}/{LOCK_KEY} is held")
    try:
        workdir.mkdir(parents=True, exist_ok=True)
        path = workdir / "mlflow.db"
        try:
            s3.download_file(bucket, DB_KEY, str(path))
        except s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] not in {"404", "NoSuchKey"}:
                raise
            path.unlink(missing_ok=True)  # first run: MLflow creates it
        try:
            yield path
        finally:
            if path.exists():
                s3.upload_file(str(path), bucket, DB_KEY)
    finally:
        lock.release()
