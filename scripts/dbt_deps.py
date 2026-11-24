"""Install the dbt packages (Elementary, dbt_utils) into dbt/headway/dbt_packages when missing.

The packages are pinned in dbt/headway/packages.yml and package-lock.yml; the directory is not
committed. The transform image installs them at build time, so nothing downloads at runtime.
Usage: python scripts/dbt_deps.py   (or `ensure()` from the other scripts and the test session)
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "dbt" / "headway"


def ensure() -> None:
    if (PROJECT / "dbt_packages" / "elementary").is_dir():
        return
    subprocess.run(
        ["uv", "run", "--project", "dbt", "dbt", "deps", "--project-dir", "dbt/headway",
         "--profiles-dir", "dbt/headway", "--target", "local"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )  # fmt: skip


if __name__ == "__main__":
    ensure()
