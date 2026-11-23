"""The chaos parameter is never created by Terraform and only ingest and transform may read it."""

import re
from pathlib import Path

MAIN = Path(__file__).resolve().parents[2] / "infra" / "main"


def _tf() -> dict[str, str]:
    return {p.name: p.read_text() for p in MAIN.glob("*.tf")}


def test_terraform_does_not_create_the_chaos_parameter() -> None:
    for name, text in _tf().items():
        assert not re.search(r'resource\s+"aws_ssm_parameter"[^{]*\{[^}]*chaos', text), name


def test_only_the_ingest_and_transform_policies_read_the_chaos_parameter() -> None:
    files = _tf()
    holders = []
    for text in files.values():
        for doc in re.finditer(r'data "aws_iam_policy_document" "(\w+)" \{', text):
            body = text[doc.end() :]
            end = re.search(r'\n(?:data|resource) "', body)
            if "local.chaos_arn" in (body[: end.start()] if end else body):
                holders.append(doc.group(1))
    assert sorted(holders) == ["ingest", "transform"]
    arn_line = next(
        line for text in files.values() for line in text.splitlines() if "chaos_arn " in line
    )
    assert "parameter${local.chaos_ssm}" in arn_line
    assert '"/headway/chaos"' in files["pipeline.tf"]


def test_the_chaos_policies_grant_read_only() -> None:
    for name in ("lambda.tf", "pipeline.tf"):
        text = _tf()[name]
        block = text[text.index('sid       = "ReadChaosSwitch"') :].split("}", 1)[0]
        assert 'actions   = ["ssm:GetParameter"]' in block
