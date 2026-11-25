"""Smoke/build cover all images always; only the push list follows enable_r3/enable_r4."""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ALL = "curate transform train api"


def make(*args: str) -> str:
    out = subprocess.run(
        ["make", "-s", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def built(target: str, *flags: str) -> list[str]:
    out = make("-n", target, "IMAGE_TAG=t", *flags)
    # The dry run prints the build loop unexpanded: `for name in <images>; do ... docker buildx`.
    loops = re.findall(r"for name in ([^;]*); do\s*\\\s*args=", out)
    assert len(loops) == 1
    return loops[0].split()


FLAGS = [
    ("false", "false", "curate transform"),
    ("true", "false", "curate transform train"),
    ("false", "true", "curate transform api"),
    ("true", "true", "curate transform train api"),
]


@pytest.mark.parametrize(("r3", "r4", "pushed"), FLAGS)
def test_smoke_and_build_are_always_all(r3: str, r4: str, pushed: str) -> None:
    flags = (f"ENABLE_R3={r3}", f"ENABLE_R4={r4}")
    assert make("images-list", *flags) == ALL
    assert built("images-build", *flags) == ALL.split()


@pytest.mark.parametrize(("r3", "r4", "pushed"), FLAGS)
def test_push_and_deploy_are_filtered(r3: str, r4: str, pushed: str) -> None:
    flags = (f"ENABLE_R3={r3}", f"ENABLE_R4={r4}")
    assert make("push-images-list", *flags) == pushed
    assert built("images-push", *flags) == pushed.split()
    assert built("deploy", *flags) == pushed.split()


def test_push_reads_tfvars(tmp_path: Path) -> None:
    tfvars = tmp_path / "t.tfvars"
    tfvars.write_text("enable_r2 = true\nenable_r3 = true  # weekly\nenable_r4 = false\n")
    assert make("push-images-list", f"TFVARS={tfvars}") == "curate transform train"


def test_missing_tfvars_means_core_only(tmp_path: Path) -> None:
    assert make("push-images-list", f"TFVARS={tmp_path / 'none'}") == "curate transform"


def test_images_smoke_includes_the_r4_transform_smoke() -> None:
    out = make("-n", "images-smoke")
    assert "scripts/smoke_image.sh transform-r4" in out
    assert "scripts/smoke_image.sh transform\n" in out  # the default (R4 off) smoke stays
    script = (ROOT / "scripts" / "smoke_image.sh").read_text()
    case = script.split("transform-r4)")[1].split("\ncurate)")[0]
    assert "HEADWAY_ENABLE_R2=true" in case and "HEADWAY_ENABLE_R4=true" in case
    assert "r['r4_tables_exported'] == 5" in case and "$MEM_CHECK" in case
    default = script.split("transform)")[1].split("transform-r4)")[0]
    assert "HEADWAY_ENABLE_R4" not in default
