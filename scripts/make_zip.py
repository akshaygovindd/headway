"""Create a deterministic zip of a directory tree (sorted entries, fixed timestamps/modes)."""

import sys
import zipfile
from pathlib import Path

FIXED_DATE = (1980, 1, 1, 0, 0, 0)


def make_zip(source: Path, target: Path) -> None:
    files = sorted(
        p
        for p in source.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            info = zipfile.ZipInfo(path.relative_to(source).as_posix(), FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            executable = path.stat().st_mode & 0o111
            info.external_attr = (0o755 if executable else 0o644) << 16
            zf.writestr(info, path.read_bytes())


if __name__ == "__main__":
    make_zip(Path(sys.argv[1]), Path(sys.argv[2]))
