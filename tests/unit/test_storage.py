from pathlib import Path

import pytest

from headway.storage import LocalStorage


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
