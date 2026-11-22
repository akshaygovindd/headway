from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from headway.storage import LocalStorage, S3Storage


def test_roundtrip_and_exists(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    assert not s.exists("a/b.bin")
    s.put_bytes("a/b.bin", b"xyz")
    assert s.exists("a/b.bin")
    assert s.get_bytes("a/b.bin") == b"xyz"


def test_list_keys_prefix_sorted(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    for k in ["raw/b", "raw/a", "manifests/x"]:
        s.put_bytes(k, b"1")
    assert s.list_keys("raw/") == ["raw/a", "raw/b"]
    assert s.list_keys("") == ["manifests/x", "raw/a", "raw/b"]


def test_list_keys_missing_root(tmp_path: Path) -> None:
    assert LocalStorage(tmp_path / "none").list_keys("") == []


@pytest.mark.parametrize("key", ["../x", "a/../../x", "/abs", ""])
def test_rejects_bad_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage(tmp_path).put_bytes(key, b"")


def _s3() -> S3Storage:
    client = boto3.client("s3", region_name="us-east-1")
    client.create_bucket(Bucket="test-bucket")
    return S3Storage("test-bucket", client)


def test_s3_roundtrip_exists_and_list() -> None:
    with mock_aws():
        s = _s3()
        assert not s.exists("a/b.bin")
        s.put_bytes("a/b.bin", b"xyz")
        s.put_bytes("raw/b", b"1")
        s.put_bytes("raw/a", b"1")
        assert s.exists("a/b.bin")
        assert s.get_bytes("a/b.bin") == b"xyz"
        assert s.list_keys("raw/") == ["raw/a", "raw/b"]


def test_s3_put_sets_content_type_and_cache_control() -> None:
    with mock_aws():
        s = _s3()
        s.put_bytes("k.json", b"{}", content_type="application/json", cache_control="max-age=30")
        head = s.client.head_object(Bucket="test-bucket", Key="k.json")
        assert head["ContentType"] == "application/json"
        assert head["CacheControl"] == "max-age=30"


def test_local_delete_is_idempotent(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    s.put_bytes("a/b.bin", b"xyz")
    s.delete("a/b.bin")
    s.delete("a/b.bin")
    assert not s.exists("a/b.bin")


def test_s3_delete() -> None:
    with mock_aws():
        s = _s3()
        s.put_bytes("a/b.bin", b"xyz")
        s.delete("a/b.bin")
        assert not s.exists("a/b.bin")
