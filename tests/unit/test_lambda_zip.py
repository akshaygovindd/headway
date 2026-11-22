import importlib.util
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ZIP = ROOT / "build" / "ingest.zip"


def _load_make_zip():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("make_zip", ROOT / "scripts" / "make_zip.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_zip


def test_make_zip_is_deterministic(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "pkg" / "__pycache__").mkdir(parents=True)
    (src / "pkg" / "b.py").write_text("b")
    (src / "pkg" / "a.py").write_text("a")
    (src / "pkg" / "__pycache__" / "a.pyc").write_bytes(b"x")
    make_zip = _load_make_zip()
    make_zip(src, tmp_path / "one.zip")
    (src / "pkg" / "a.py").touch()  # new mtime must not change the output
    make_zip(src, tmp_path / "two.zip")
    assert (tmp_path / "one.zip").read_bytes() == (tmp_path / "two.zip").read_bytes()
    with zipfile.ZipFile(tmp_path / "one.zip") as zf:
        assert zf.namelist() == ["pkg/a.py", "pkg/b.py"]


@pytest.mark.skipif(not ZIP.exists(), reason="run `make lambda-zip` first")
def test_built_zip_contents_and_size() -> None:
    with zipfile.ZipFile(ZIP) as zf:
        names = zf.namelist()
    assert "headway/ingest/handler.py" in names
    assert "config/cities/boston.yaml" in names
    assert not [n for n in names if n.split("/")[0] in {"boto3", "botocore", "s3transfer"}]
    assert not [n for n in names if "__pycache__" in n]
    assert ZIP.stat().st_size < 50 * 1024 * 1024
