import importlib.util
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_dbt_docs.py"
_spec = importlib.util.spec_from_file_location("check_dbt_docs", SCRIPT)
assert _spec and _spec.loader
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
check_manifest = _mod.check_manifest


def _model(desc: str, col_desc: str = "a column") -> dict[str, Any]:
    return {
        "resource_type": "model",
        "name": "m",
        "description": desc,
        "columns": {"id": {"description": col_desc}},
    }


def _grain_test(model_uid: str) -> dict[str, Any]:
    return {
        "resource_type": "test",
        "test_metadata": {"name": "unique_grain"},
        "attached_node": model_uid,
    }


def test_documented_and_graded_model_passes() -> None:
    manifest = {"nodes": {"model.p.m": _model("desc"), "test.p.t": _grain_test("model.p.m")}}
    assert check_manifest(manifest) == []


def test_missing_description_and_grain_test_are_reported() -> None:
    problems = check_manifest({"nodes": {"model.p.m": _model("")}})
    assert "m: missing model description" in problems
    assert "m: missing unique_grain test" in problems


def test_missing_column_description_is_reported() -> None:
    manifest = {"nodes": {"model.p.m": _model("desc", ""), "test.p.t": _grain_test("model.p.m")}}
    assert check_manifest(manifest) == ["m.id: missing column description"]


def test_other_tests_do_not_count_as_grain_test() -> None:
    other = {
        "resource_type": "test",
        "test_metadata": {"name": "not_null"},
        "attached_node": "model.p.m",
    }
    manifest = {"nodes": {"model.p.m": _model("desc"), "test.p.t": other}}
    assert check_manifest(manifest) == ["m: missing unique_grain test"]
