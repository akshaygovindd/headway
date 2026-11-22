"""Fail when a dbt model lacks a description, a column description or a unique_grain test.

Usage: python scripts/check_dbt_docs.py [path/to/manifest.json]
"""

import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST = Path("dbt/headway/target/manifest.json")


def check_manifest(manifest: dict[str, Any]) -> list[str]:
    """Return one message per documentation problem found."""
    nodes: dict[str, Any] = manifest.get("nodes", {})
    graded = {
        uid
        for uid, node in nodes.items()
        if node.get("resource_type") == "test"
        and node.get("test_metadata", {}).get("name") == "unique_grain"
    }
    graded_models: set[str] = set()
    for uid in graded:
        attached = nodes[uid].get("attached_node")
        if attached:
            graded_models.add(attached)
        else:
            graded_models.update(nodes[uid].get("depends_on", {}).get("nodes", []))

    problems: list[str] = []
    for uid, node in sorted(nodes.items()):
        if node.get("resource_type") != "model":
            continue
        name = node.get("name", uid)
        if not str(node.get("description", "")).strip():
            problems.append(f"{name}: missing model description")
        if uid not in graded_models:
            problems.append(f"{name}: missing unique_grain test")
        for col_name, col in sorted(node.get("columns", {}).items()):
            if not str(col.get("description", "")).strip():
                problems.append(f"{name}.{col_name}: missing column description")
        if not node.get("columns"):
            problems.append(f"{name}: no documented columns")
    return problems


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else DEFAULT_MANIFEST
    if not path.exists():
        print(f"manifest not found: {path} (run dbt parse or dbt build first)", file=sys.stderr)
        return 2
    problems = check_manifest(json.loads(path.read_text()))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"{len(problems)} documentation problem(s)", file=sys.stderr)
        return 1
    print("dbt docs check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
