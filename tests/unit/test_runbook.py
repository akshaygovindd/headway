"""The runbook covers every alarm and every simulation, alarms link to it, and its links resolve."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
RUNBOOK_URL = "https://github.com/akshaygovindd/headway/blob/main/docs/RUNBOOK.md#"


def slug(heading: str) -> str:
    """GitHub's anchor for a heading."""
    text = re.sub(r"[^\w\s-]", "", heading.strip().lower())
    return re.sub(r"\s", "-", text)


def anchors(path: Path) -> set[str]:
    in_code = False
    found = set()
    for line in path.read_text().splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and (m := re.match(r"#{1,6}\s+(.*)", line)):
            found.add(slug(m.group(1)))
    return found


def alarms() -> list[tuple[str, str]]:
    """(alarm name template, description) for each CloudWatch alarm in alerts.tf."""
    text = (ROOT / "infra/main/alerts.tf").read_text()
    found = re.findall(
        r'alarm_name\s*=\s*"([^"]+)".*?alarm_description\s*=\s*"([^"]+)"', text, re.S
    )
    assert len(found) == 11
    return found


def test_every_alarm_description_links_to_a_runbook_section() -> None:
    known = anchors(DOCS / "RUNBOOK.md")
    for name, description in alarms():
        assert RUNBOOK_URL in description, name
        anchor = description.split(RUNBOOK_URL, 1)[1].strip()
        assert anchor in known, f"{name}: no RUNBOOK section for #{anchor}"
        assert anchor.startswith("alarm-headway-")


def test_every_alarm_has_exactly_one_section_named_after_it() -> None:
    known = anchors(DOCS / "RUNBOOK.md")
    names = {
        "headway-ingest-rt-errors", "headway-ingest-rt-invocations-low", "headway-curate-errors",
        "headway-static-errors", "headway-transform-errors", "headway-transform-not-succeeded", "headway-transform-memory-high",
        "headway-feed-errors", "headway-gold-stale", "headway-missed-minutes",
        "headway-arrival-coverage-low",
    }  # fmt: skip
    assert {slug(f"Alarm: {n}") for n in names} <= known
    resolved = {
        n.replace("${local.function_name}", "headway-ingest-rt")
        .replace("${local.curate_name}", "headway-curate")
        .replace("${local.static_name}", "headway-static")
        .replace("${local.transform_name}", "headway-transform")
        for n, _ in alarms()
    }
    assert resolved == names


def test_every_simulation_has_a_runbook_section_and_a_test_file() -> None:
    text = (DOCS / "RUNBOOK.md").read_text()
    sections = re.findall(r"^### Simulation: (.+)$", text, re.M)
    assert len(sections) == 6
    for test in sorted((ROOT / "tests/simulations").glob("test_*.py")):
        assert f"tests/simulations/{test.name}" in text, f"{test.name} is not in the runbook"
    assert len(list((ROOT / "tests/simulations").glob("test_*.py"))) == 6


def test_every_section_has_the_same_parts() -> None:
    text = (DOCS / "RUNBOOK.md").read_text()
    for part in re.split(r"^### (?:Alarm|Simulation): ", text, flags=re.M)[1:]:
        for label in ("**Meaning:**", "**First 3 commands:**", "**Likely causes:**", "**Fix:**"):
            assert label in part.split("\n## ")[0], label
        assert "**Recovery:**" in part.split("\n## ")[0]


def test_the_drill_commands_are_exact() -> None:
    text = (DOCS / "RUNBOOK.md").read_text()
    for mode in ("feed_down", "md_down"):
        assert f'\\"mode\\":\\"{mode}\\",\\"until\\":\\"$UNTIL\\"' in text
    assert "date -u -v+15M" in text and "aws ssm put-parameter --name /headway/chaos" in text


def test_links_between_the_docs_resolve() -> None:
    for path in (DOCS / "RUNBOOK.md", DOCS / "INCIDENTS.md"):
        text = path.read_text()
        for target, anchor in re.findall(r"\]\(([\w./-]*\.md)?#([\w-]+)\)", text):
            file = DOCS / target if target else path
            assert anchor in anchors(file), f"{path.name}: {target or ''}#{anchor}"


def test_the_cost_forecast_alert_is_in_terraform_and_the_runbook() -> None:
    budget = (ROOT / "infra/main/budget.tf").read_text()
    assert (
        'notification_type          = "FORECASTED"' in budget
        and "threshold                  = 15" in budget
    )
    assert "### Budget forecast alert" in (DOCS / "RUNBOOK.md").read_text()


def test_the_coverage_alarm_exists_only_with_release_2() -> None:
    text = (ROOT / "infra/main/alerts.tf").read_text()
    block = text.split('resource "aws_cloudwatch_metric_alarm" "arrival_coverage_low"')[1]
    assert "count = var.enable_r2 ? 1 : 0" in block
    assert 'treat_missing_data  = var.enable_r2 ? "missing" : "notBreaching"' in block
