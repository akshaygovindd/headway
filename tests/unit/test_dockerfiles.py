import importlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = {
    "curate": "headway.curate.handler.curate_handler",
    "transform": "headway_transform.handler.lambda_handler",
}


def _cmd(name: str) -> str:
    text = (ROOT / "docker" / f"{name}.Dockerfile").read_text()
    match = re.search(r'^CMD \["([^"]+)"\]', text, re.MULTILINE)
    assert match, f"{name}: no CMD"
    return match.group(1)


@pytest.mark.parametrize("name", sorted(DOCKERFILES))
def test_cmd_names_an_importable_handler(name: str) -> None:
    assert _cmd(name) == DOCKERFILES[name]
    module, func = DOCKERFILES[name].rsplit(".", 1)
    assert callable(getattr(importlib.import_module(module), func))


def test_static_handler_exists_for_the_curate_image() -> None:
    from headway.curate import handler

    assert callable(handler.static_handler)


def _docker_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    return subprocess.run(["docker", "info"], capture_output=True, check=False).returncode == 0


@pytest.mark.skipif(not _docker_ready(), reason="docker daemon not available")
@pytest.mark.parametrize("name", sorted(DOCKERFILES))
def test_dockerfile_passes_build_checks(name: str) -> None:
    proc = subprocess.run(
        ["docker", "build", "--check", "-f", f"docker/{name}.Dockerfile", "."],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stdout + proc.stderr


PINNED_DUCKDB = "1.5.6"  # MotherDuck support constraint, see ADR-012


def test_duckdb_is_pinned_to_the_motherduck_supported_version() -> None:
    import duckdb

    assert duckdb.__version__ == PINNED_DUCKDB
    for rel in ("pyproject.toml", "dbt/pyproject.toml"):
        assert f'"duckdb=={PINNED_DUCKDB}"' in (ROOT / rel).read_text(), rel
    assert (
        f"duckdb.__version__ == '{PINNED_DUCKDB}'"
        in (ROOT / "docker" / "transform.Dockerfile").read_text()
    )
