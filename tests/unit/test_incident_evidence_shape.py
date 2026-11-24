"""The evidence document dbt builds (pinned by the dbt unit test incident_evidence_json) is the
shape the template and the number check read: the two sides cannot drift apart."""

import json
from pathlib import Path
from typing import Any

import yaml

from headway_transform import incident_numcheck as nc
from headway_transform import incident_summaries as isum

UNIT_TESTS = Path(__file__).resolve().parents[2] / "dbt/headway/models/marts/_unit_tests.yml"


def _dbt_evidence() -> str:
    doc = yaml.safe_load(UNIT_TESTS.read_text())
    test = next(t for t in doc["unit_tests"] if t["name"] == "incident_evidence_json")
    return str(test["expect"]["rows"][0]["evidence_json"])


def test_template_and_check_read_the_dbt_evidence() -> None:
    raw = _dbt_evidence()
    evidence: dict[str, Any] = json.loads(raw)
    text = isum.template_summary(evidence)
    assert text.startswith("Red (direction 0): incident from 08:00 to 10:00 (2 hours).")
    assert 'A service alert was active: "Shuttle buses replace trains".' in text
    assert "reached 420 s at 09:00, 380 s above the usual 40 s" in text
    assert "headway gaps reached 35% at 08:00" in text
    assert nc.check(text, raw).ok, nc.check(text, raw).problems
    # and the fixed instruction's numbers rule applies to what the LLM would be given
    assert not nc.check(text + " Delays hit 61 s.", raw).ok
