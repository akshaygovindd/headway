"""The transform role must allow every S3 action the run-state module performs, on its prefixes."""

import fnmatch
import re
from pathlib import Path

from headway_transform import observability, state

PIPELINE = (Path(__file__).resolve().parents[2] / "infra" / "main" / "pipeline.tf").read_text()


def _transform_policy() -> str:
    start = PIPELINE.index('data "aws_iam_policy_document" "transform" {')
    end = PIPELINE.find('\nresource "', start)
    return PIPELINE[start:end]


def _local(name: str) -> str:
    return re.search(rf'^\s*{name}\s*=\s*"([^"]+)"', PIPELINE, re.M).group(1)  # type: ignore[union-attr]


def _statement(sid: str) -> str:
    body = _transform_policy()
    return body[body.index(f'sid       = "{sid}"') :].split("\n  }\n", 1)[0]


def _prefix_patterns() -> list[str]:
    return [_local("state_prefix"), _local("elementary_state_prefix")]


def test_state_module_actions_are_the_ones_checked() -> None:
    src = Path(state.__file__).read_text()
    used = set(re.findall(r"s3\.(put_object|get_object|delete_object|head_object)\(", src))
    # download_file streams to disk: it issues GetObject (and a HeadObject, also s3:GetObject)
    if "s3.download_file(" in src:
        used.add("get_object")
    used.add("list_objects_v2")
    assert used == {"put_object", "get_object", "list_objects_v2"}, used


def test_every_state_folder_is_covered_for_list_get_and_put() -> None:
    folders = [state.TRANSFORM_RUNS.prefix] + [t.prefix for t, _ in observability.PERSISTED]
    assert any("elementary" in f for f in folders)
    list_stmt, read_stmt, write_stmt = (
        _statement("ListRawInputs"),
        _statement("ReadRawInputs"),
        _statement("WriteRunState"),
    )
    assert '"s3:ListBucket"' in list_stmt and '"s3:GetObject"' in read_stmt
    assert '"s3:PutObject"' in write_stmt
    assert "local.raw_data_prefix" in list_stmt and "local.raw_data_prefix" in read_stmt
    assert "local.state_prefixes" in write_stmt
    concat = re.search(r"raw_data_prefix = concat\((.*?)\n  \)", PIPELINE, re.S)
    assert concat and "state_prefixes" in concat.group(1)
    for folder in folders:
        # list uses Prefix "<folder>/", objects live under "<folder>/dt=.../rows.parquet"
        key = f"{folder}/dt=2026-11-24/rows.parquet"
        assert any(fnmatch.fnmatch(f"{folder}/", f"{p}/*") for p in _prefix_patterns()), folder
        assert any(fnmatch.fnmatch(key, f"{p}/*") for p in _prefix_patterns()), folder


def test_the_closed_day_facts_prefix_is_covered_for_list_get_and_put() -> None:
    from headway_transform import facts_state

    assert _local("facts_state_prefix") == facts_state.PREFIX
    concat = re.search(r"state_prefixes = concat\((.*?)\n  \)", PIPELINE, re.S)
    assert concat and "local.facts_state_prefix" in concat.group(1)
    assert "var.enable_r2" in concat.group(1)  # only Release 2 writes them
    key = facts_state.key_for("fct_headways", 1, __import__("datetime").date(2026, 11, 20))
    assert fnmatch.fnmatch(key, f"{facts_state.PREFIX}/*")
    # the plan lists "<prefix>/<table>/metric_version=N/"
    assert fnmatch.fnmatch(f"{facts_state.PREFIX}/fct_headways/metric_version=1/", "state/facts/*")


def test_the_daily_report_can_be_written_and_checked_at_its_one_key() -> None:
    """The report upload was AccessDenied: no statement covered public/<city>/observability/."""
    key = observability.report_key("boston")
    assert key == "public/boston/observability/index.html"
    assert _local("observability_report_key").replace("${var.city_id}", "boston") == key
    stmt = _statement("ReadAndWriteObservabilityReport")
    # PutObject uploads it; HeadObject (report_due) needs GetObject, else every run regenerates it
    assert '"s3:PutObject"' in stmt and '"s3:GetObject"' in stmt
    assert "aws_s3_bucket.public.arn}/${local.observability_report_key}" in stmt
    assert "*" not in stmt  # one object, not a prefix
    src = Path(observability.__file__).read_text()
    assert set(re.findall(r"s3\.(put_object|get_object|head_object)\(", src)) == {
        "put_object",
        "head_object",
    }
