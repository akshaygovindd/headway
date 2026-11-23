"""Object storage abstraction with a local filesystem implementation."""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class Storage(Protocol):
    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None: ...

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Write ``data`` only if ``key`` does not exist; False when it already does."""
        ...

    def get_bytes(self, key: str) -> bytes: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


# 412 when the object exists; 409 when a concurrent conditional write to the key is in flight.
_CONDITION_CONFLICT = {"PreconditionFailed", "ConditionalRequestConflict"}


class LocalStorage:
    """Stores objects as files under a root directory."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        if not key or key.startswith("/") or ".." in key.split("/"):
            raise ValueError(f"invalid storage key: {key!r}")
        return self.root / key

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as handle:  # exclusive create: atomic across processes
                handle.write(data)
        except FileExistsError:
            return False
        return True

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def list_keys(self, prefix: str) -> list[str]:
        if not self.root.exists():
            return []
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)


class S3Storage:
    """Stores objects in an S3 bucket."""

    def __init__(self, bucket: str, client: "S3Client | None" = None) -> None:
        self.bucket = bucket
        if client is None:
            import boto3

            client = boto3.client("s3")
        self.client = client

    def put_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
        cache_control: str | None = None,
    ) -> None:
        extra: dict[str, Any] = {}
        if content_type is not None:
            extra["ContentType"] = content_type
        if cache_control is not None:
            extra["CacheControl"] = cache_control
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **extra)

    def put_if_absent(self, key: str, data: bytes) -> bool:
        """Conditional create (``IfNoneMatch='*'``); False when the object already exists."""
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, IfNoneMatch="*")
        except self.client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in _CONDITION_CONFLICT:
                return False
            raise
        return True

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def list_keys(self, prefix: str) -> list[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents", []))
        return sorted(keys)

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except self.client.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
