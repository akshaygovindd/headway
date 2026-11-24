import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "dbt_deps.py"


@pytest.fixture(scope="session", autouse=True)
def dbt_packages() -> None:
    """Every test that builds the dbt project needs its packages; install them once if missing."""
    spec = importlib.util.spec_from_file_location("dbt_deps", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ensure()
