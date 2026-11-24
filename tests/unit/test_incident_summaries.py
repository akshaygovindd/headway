"""Incident summaries: the template path, the LLM path behind a fake client (no network), the
blocking number check, idempotency per incident version, the daily cap and the open-incident
refresh."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3
import duckdb
import pytest
from moto import mock_aws

from headway_transform import incident_numcheck, incident_summaries, state

NOW = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)


class FakeLlm:
    """Records calls; answers from a list, else with the (valid) template text."""

    model = "fake-1"

    def __init__(self, answers: list[str] | None = None, fail: bool = False) -> None:
        self.answers = list(answers or [])
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    def complete(self, instruction: str, evidence_json: str) -> str:
        self.calls.append((instruction, evidence_json))
        if self.fail:
            raise incident_summaries.LlmError("boom")
        if self.answers:
            return self.answers.pop(0)
        return incident_summaries.template_summary(json.loads(evidence_json))


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Any:
    for k, v in {"AWS_ACCESS_KEY_ID": "t", "AWS_SECRET_ACCESS_KEY": "t",
                 "AWS_DEFAULT_REGION": "us-east-1"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    with mock_aws():
        client = boto3.client("s3")
        client.create_bucket(Bucket="raw")
        yield client


def _db(path: Path, versions: dict[str, tuple[str, bool]] | None = None) -> str:
    """agg_incidents as dbt builds it, with incidents {id: (hash, is_open)}."""
    con = duckdb.connect(str(path))
    con.execute(
        "create table agg_incidents (incident_id varchar, evidence_hash varchar, "
        "incident_start_ts timestamptz, is_open boolean, evidence_json varchar, "
        "summary_text varchar, template_used boolean, number_check varchar, "
        "rejection_reason varchar, llm_model varchar, summary_generated_at timestamptz, "
        "summary_evidence_hash varchar, summary_stale boolean)"
    )
    for i, (inc, (version, is_open)) in enumerate((versions or {"i1": ("h1", False)}).items()):
        evidence = incident_summaries._smoke_evidence(
            "Red" if i % 2 == 0 else "Blue", "15:00", "16:00"
        )
        evidence["is_open"] = is_open
        con.execute(
            "insert into agg_incidents (incident_id, evidence_hash, incident_start_ts, is_open, "
            "evidence_json) values (?, ?, ?, ?, ?)",
            [inc, version, NOW - timedelta(hours=3, minutes=i), is_open, json.dumps(evidence)],
        )
    con.close()
    return str(path)


def _row(db: str, incident_id: str = "i1") -> dict[str, Any]:
    con = duckdb.connect(db, read_only=True)
    cur = con.execute(
        "select * replace (cast(incident_start_ts as varchar) as incident_start_ts, "
        "cast(summary_generated_at as varchar) as summary_generated_at) "
        "from agg_incidents where incident_id = ?",
        [incident_id],
    )
    names = [d[0] for d in cur.description]
    row = dict(zip(names, cur.fetchone() or (), strict=True))
    con.close()
    return row


def test_without_a_key_the_template_is_used(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "a.duckdb")
    out = incident_summaries.summarize(s3, "raw", db, NOW, client=None)
    row = _row(db)
    assert out["incident_summaries_template"] == 1 and out["incident_llm_calls"] == 0
    assert row["template_used"] is True and row["number_check"] == "not_run"
    assert row["rejection_reason"] == "no_api_key" and row["llm_model"] is None
    assert row["summary_stale"] is False and row["summary_evidence_hash"] == "h1"
    evidence = json.loads(
        duckdb.connect(db).execute("select evidence_json from agg_incidents").fetchone()[0]
    )  # type: ignore[index]
    assert row["summary_text"] == incident_summaries.template_summary(evidence)
    assert incident_numcheck.check(row["summary_text"], evidence).ok


def test_llm_text_that_passes_the_check_is_used(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "b.duckdb")
    text = "Red was late: the median deviation reached 420 s at 15:00."
    client = FakeLlm([text])
    out = incident_summaries.summarize(s3, "raw", db, NOW, client=client)
    row = _row(db)
    assert row["summary_text"] == text and row["template_used"] is False
    assert row["number_check"] == "passed" and row["llm_model"] == "fake-1"
    assert out["incident_summaries_llm"] == 1 and out["incident_llm_rejections"] == 0
    # the LLM got the fixed instruction and only the evidence JSON
    instruction, evidence_json = client.calls[0]
    assert instruction == incident_summaries.INSTRUCTION
    assert json.loads(evidence_json)["line"] == "Red"


def test_a_failed_check_falls_back_to_the_template_and_counts(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "c.duckdb")
    out = incident_summaries.summarize(
        s3, "raw", db, NOW, client=FakeLlm(["Delays reached 99999 s because of rain."])
    )
    row = _row(db)
    assert row["template_used"] is True and row["number_check"] == "rejected"
    assert "99999" in row["rejection_reason"] and "causal" in row["rejection_reason"]
    assert "99999" not in row["summary_text"]
    assert out["incident_llm_rejections"] == 1 and out["incident_llm_calls"] == 1


def test_a_provider_error_uses_the_template(s3: Any, tmp_path: Path) -> None:
    db = _db(tmp_path / "d.duckdb")
    out = incident_summaries.summarize(s3, "raw", db, NOW, client=FakeLlm(fail=True))
    row = _row(db)
    assert row["template_used"] is True and row["rejection_reason"] == "llm_error: LlmError"
    assert out["incident_llm_errors"] == 1


def test_idempotent_per_incident_version(s3: Any, tmp_path: Path) -> None:
    client = FakeLlm()
    db = _db(tmp_path / "e.duckdb")
    incident_summaries.summarize(s3, "raw", db, NOW, client=client)
    assert len(client.calls) == 1
    # the next run starts from a fresh warehouse: the persisted state prevents a second call
    db2 = _db(tmp_path / "e2.duckdb")
    out = incident_summaries.summarize(s3, "raw", db2, NOW + timedelta(minutes=30), client=client)
    assert len(client.calls) == 1 and out["incident_summaries_generated"] == 0
    assert _row(db2)["summary_text"] is not None  # filled from the persisted summary
    # a new version of a closed incident is summarised again
    db3 = _db(tmp_path / "e3.duckdb", {"i1": ("h2", False)})
    incident_summaries.summarize(s3, "raw", db3, NOW + timedelta(hours=1), client=client)
    assert len(client.calls) == 2 and _row(db3)["summary_evidence_hash"] == "h2"


def test_open_incident_is_refreshed_every_three_hours(s3: Any, tmp_path: Path) -> None:
    client = FakeLlm()
    incident_summaries.summarize(
        s3, "raw", _db(tmp_path / "f1.duckdb", {"i1": ("h1", True)}), NOW, client=client
    )
    assert len(client.calls) == 1  # no summary yet: generated at once
    later = NOW + timedelta(hours=1)
    db = _db(tmp_path / "f2.duckdb", {"i1": ("h2", True)})
    incident_summaries.summarize(s3, "raw", db, later, client=client)
    row = _row(db)
    assert len(client.calls) == 1  # still open, last summary 1 h old
    assert row["summary_stale"] is True and row["summary_evidence_hash"] == "h1"
    db = _db(tmp_path / "f3.duckdb", {"i1": ("h3", True)})
    incident_summaries.summarize(s3, "raw", db, NOW + timedelta(hours=3), client=client)
    assert len(client.calls) == 2 and _row(db)["summary_stale"] is False
    # closing makes a new version summarised at once
    db = _db(tmp_path / "f4.duckdb", {"i1": ("h4", False)})
    incident_summaries.summarize(s3, "raw", db, NOW + timedelta(hours=3, minutes=5), client=client)
    assert len(client.calls) == 3


def test_daily_call_cap_across_runs(s3: Any, tmp_path: Path) -> None:
    client = FakeLlm()
    versions = {"i1": ("h1", False), "i2": ("h2", False), "i3": ("h3", False)}
    db = _db(tmp_path / "g.duckdb", versions)
    out = incident_summaries.summarize(s3, "raw", db, NOW, client=client, cap=2)
    assert len(client.calls) == 2 and out["incident_llm_capped"] == 1
    assert out["incident_summaries_template"] == 1
    # the template of the capped one says why; the cap is spent for the rest of the day
    capped = [
        _row(db, i) for i in versions if _row(db, i)["rejection_reason"] == "daily_cap_reached"
    ]
    assert len(capped) == 1 and capped[0]["template_used"] is True
    # next day the cap resets, and the persisted counts of yesterday are not today's
    db2 = _db(tmp_path / "g2.duckdb", {"i4": ("h4", False)})
    incident_summaries.summarize(s3, "raw", db2, NOW + timedelta(days=1), client=client, cap=2)
    assert len(client.calls) == 3


def test_load_api_key_absent_or_present() -> None:
    class Ssm:
        def __init__(self, value: str | None) -> None:
            self.value = value

        def get_parameter(self, **kw: Any) -> dict[str, Any]:
            assert kw == {"Name": "/headway/openai_api_key", "WithDecryption": True}
            if self.value is None:
                raise RuntimeError("ParameterNotFound")
            return {"Parameter": {"Value": self.value}}

    assert incident_summaries.load_api_key(Ssm(None)) is None
    assert incident_summaries.load_api_key(Ssm("  ")) is None
    assert incident_summaries.load_api_key(Ssm("sk-test")) == "sk-test"
    assert incident_summaries.build_client(Ssm(None)) is None
    client = incident_summaries.build_client(Ssm("sk-test"))
    assert client is not None and client.model == incident_summaries.DEFAULT_MODEL


def test_model_name_comes_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_INCIDENT_LLM_MODEL", "some-model")

    class Ssm:
        def get_parameter(self, **kw: Any) -> dict[str, Any]:
            return {"Parameter": {"Value": "k"}}

    client = incident_summaries.build_client(Ssm())
    assert client is not None and client.model == "some-model"


def test_smoke_fake_rejects_first_answer_only() -> None:
    fake = incident_summaries.SmokeLlm()
    ev = incident_summaries._smoke_evidence("Red", "15:00", "16:00")
    assert not incident_numcheck.check(fake.complete("", json.dumps(ev)), ev).ok
    assert incident_numcheck.check(fake.complete("", json.dumps(ev)), ev).ok


def test_state_table_declares_every_column() -> None:
    assert list(incident_summaries.INCIDENT_SUMMARIES.columns)[:2] == [
        "incident_id",
        "evidence_hash",
    ]
    assert isinstance(incident_summaries.INCIDENT_SUMMARIES, state.StateTable)
