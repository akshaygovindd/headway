"""`make deploy` sets every image in use to the new tag and stops if one did not take."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/set_image_tags.sh"
REPO = "381232271655.dkr.ecr.us-east-1.amazonaws.com/headway"


def tfvars(tmp_path: Path, *names: str) -> Path:
    f = tmp_path / "terraform.tfvars"
    f.write_text(
        "".join(f'{n}_image_uri = "{REPO}-{n}:old"\n' for n in names) + "enable_r2 = true\n"
    )
    return f


def run(f: Path, r3: str, r4: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), str(f), "abc1234", r3, r4], capture_output=True, text=True, check=False
    )


def test_sets_curate_and_transform_only_when_both_switches_are_off(tmp_path: Path) -> None:
    f = tfvars(tmp_path, "curate", "transform")
    assert run(f, "false", "false").returncode == 0
    text = f.read_text()
    assert f'"{REPO}-curate:abc1234"' in text and f'"{REPO}-transform:abc1234"' in text
    assert "enable_r2 = true" in text


def test_sets_the_train_and_api_tags_when_their_switches_are_on(tmp_path: Path) -> None:
    f = tfvars(tmp_path, "curate", "transform", "train", "api")
    assert run(f, "true", "true").returncode == 0
    text = f.read_text()
    assert ":old" not in text and text.count(":abc1234") == 4


def test_fails_naming_train_when_r3_is_on_and_its_line_is_missing(tmp_path: Path) -> None:
    f = tfvars(tmp_path, "curate", "transform")
    p = run(f, "true", "false")
    assert p.returncode == 1 and "train" in p.stderr and "api" not in p.stderr.split(":")[-1]


def test_fails_naming_api_when_r4_is_on_and_its_tag_is_in_another_repository(
    tmp_path: Path,
) -> None:
    f = tfvars(tmp_path, "curate", "transform", "train")
    f.write_text(f.read_text() + f'api_image_uri = "{REPO}-train:old"\n')
    p = run(f, "true", "true")
    assert p.returncode == 1 and p.stderr.strip().endswith("api")


def test_a_switch_that_is_off_does_not_need_its_line(tmp_path: Path) -> None:
    f = tfvars(tmp_path, "curate", "transform")
    assert run(f, "false", "false").returncode == 0


def test_a_missing_curate_line_fails_even_with_both_switches_off(tmp_path: Path) -> None:
    f = tfvars(tmp_path, "transform")
    p = run(f, "false", "false")
    assert p.returncode == 1 and "curate" in p.stderr


def test_make_deploy_calls_the_script_with_the_switches() -> None:
    text = (ROOT / "Makefile").read_text()
    assert "scripts/set_image_tags.sh $(TFVARS) $(IMAGE_TAG)" in text
    assert "$(filter true,$(ENABLE_R3))" in text and "$(filter true,$(ENABLE_R4))" in text
