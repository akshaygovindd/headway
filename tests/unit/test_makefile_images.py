"""`make images-list` prints only the images whose ECR repositories Terraform creates."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def images(*flags: str) -> str:
    out = subprocess.run(
        ["make", "-s", "images-list", *flags], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


@pytest.mark.parametrize(
    ("r3", "r4", "expected"),
    [
        ("false", "false", "curate transform"),
        ("true", "false", "curate transform train"),
        ("false", "true", "curate transform api"),
        ("true", "true", "curate transform train api"),
    ],
)
def test_flags(r3: str, r4: str, expected: str) -> None:
    assert images(f"ENABLE_R3={r3}", f"ENABLE_R4={r4}") == expected


def test_reads_tfvars(tmp_path: Path) -> None:
    tfvars = tmp_path / "t.tfvars"
    tfvars.write_text("enable_r2 = true\nenable_r3 = true  # weekly\nenable_r4 = false\n")
    assert images(f"TFVARS={tfvars}") == "curate transform train"


def test_missing_tfvars_means_core_only(tmp_path: Path) -> None:
    assert images(f"TFVARS={tmp_path / 'none'}") == "curate transform"
