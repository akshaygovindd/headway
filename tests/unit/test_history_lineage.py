"""Every history family that reads reconstructed arrival times is at the shared bumped version.

A change of how arrivals are reconstructed changes every closed-day aggregate built on them, so
those families must be rebuilt together, at one version, instead of mixing methods across days.
The dependents are found from the dbt models themselves (ref() calls, including those inside the
macros a model calls), not from a list kept here, so a new aggregate that reads arrivals fails this
test until its family is moved to ARRIVAL_TIMES_VERSION.
"""

import re
from pathlib import Path

from headway_transform import history

DBT = Path(__file__).resolve().parents[2] / "dbt" / "headway"
ROOTS = {"fct_reconstructed_arrivals", "fct_headways"}
REF = re.compile(r"ref\(\s*'([^']+)'\s*\)")
MACRO = re.compile(r"\{%-?\s*macro\s+(\w+)\s*\(")


def _graph() -> dict[str, set[str]]:
    macros: dict[str, set[str]] = {}
    for f in (DBT / "macros").glob("*.sql"):
        text = f.read_text()
        starts = [(m.start(), m.group(1)) for m in MACRO.finditer(text)]
        for i, (pos, name) in enumerate(starts):
            end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
            macros[name] = set(REF.findall(text[pos:end]))
    graph: dict[str, set[str]] = {}
    for f in (DBT / "models").rglob("*.sql"):
        text = f.read_text()
        deps = set(REF.findall(text))
        for name, refs in macros.items():
            if re.search(rf"\b{name}\s*\(", text):
                deps |= refs
        graph[f.stem] = deps
    return graph


def _depends_on_arrivals(model: str, graph: dict[str, set[str]]) -> bool:
    seen: set[str] = set()
    todo = [model]
    while todo:
        for dep in graph.get(todo.pop(), set()):
            if dep in ROOTS:
                return True
            if dep not in seen:
                seen.add(dep)
                todo.append(dep)
    return False


def test_every_family_that_reads_arrival_times_shares_the_bumped_version() -> None:
    graph = _graph()
    dependents = {
        table: family
        for table, family in history.HISTORY_TABLES.items()
        if _depends_on_arrivals(table, graph)
    }
    # the lineage is real: the families the models are known to read arrivals through are found
    assert {
        "agg_actual_deviation_daily",
        "agg_headway_adherence_daily",
        "agg_ett_daily",
        "agg_arrival_reconstruction_daily",
    } <= set(dependents)
    wrong = {
        f"{table} ({family}): {history.METRIC_VERSIONS[family]}"
        for table, family in dependents.items()
        if history.METRIC_VERSIONS[family] != history.ARRIVAL_TIMES_VERSION
    }
    assert not wrong, f"not at version {history.ARRIVAL_TIMES_VERSION}: {sorted(wrong)}"


def test_a_family_that_does_not_read_arrivals_is_not_dragged_along() -> None:
    graph = _graph()
    assert not _depends_on_arrivals("agg_stop_deviation_daily", graph)
    assert history.METRIC_VERSIONS["stop_deviation"] != history.ARRIVAL_TIMES_VERSION


def test_the_tables_of_one_family_agree_on_whether_they_read_arrivals() -> None:
    # two tables of one family (actual_deviation daily and hourly) cannot disagree
    families = {}
    for table, family in history.HISTORY_TABLES.items():
        families.setdefault(family, []).append(table)
    graph = _graph()
    for tables in families.values():
        flags = {_depends_on_arrivals(t, graph) for t in tables}
        assert len(flags) == 1, tables
