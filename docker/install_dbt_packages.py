"""Build-time only: install the dbt packages of the project and of the `edr` report CLI.

Runs in the transform image's build stage with the dbt environment on PYTHONPATH. Versions come
from packages.yml / package-lock.yml (ours) and the pinned elementary-data wheel (edr's own); the
function never downloads anything at runtime.
"""

import os
import sys

import elementary
from dbt.cli.main import dbtRunner

PROJECT = sys.argv[1]
EDR_PROJECT = os.path.join(os.path.dirname(elementary.__file__), "monitor", "dbt_project")

for project in (PROJECT, EDR_PROJECT):
    result = dbtRunner().invoke(["deps", "--project-dir", project, "--profiles-dir", PROJECT])
    assert result.success, (project, result.exception)

assert os.path.isdir(f"{PROJECT}/dbt_packages/elementary"), "elementary package missing"
assert os.path.isdir(f"{EDR_PROJECT}/dbt_packages/elementary"), "edr internal package missing"
print("dbt packages installed:", sorted(os.listdir(f"{PROJECT}/dbt_packages")))
