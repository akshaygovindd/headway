"""Simulation 6: MotherDuck is unavailable (the md_down drill). Gold is still exported to S3, the
publish error is logged, and the run counts as failed for the transform-success objective;
the next run, after the drill, recovers."""

import json
import logging
from datetime import UTC, datetime
from typing import Any

import boto3
import pytest
from moto import mock_aws

from headway_transform import handler, publish, state

from .world import World

GOLD = "public/boston/gold"
SLO_AS_OF = "2026-11-22T16:59:30Z"
DRILL_START, DRILL_END = datetime(2026, 11, 22, 15, 20, tzinfo=UTC), "2026-11-22T15:35:00Z"


@pytest.fixture(scope="module")
def world(tmp_path_factory: pytest.TempPathFactory) -> Any:
    tmp = tmp_path_factory.mktemp("md_down")
    mp = pytest.MonkeyPatch()
    for k, v in {
        "AWS_DEFAULT_REGION": "us-east-1", "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test", "RAW_BUCKET": "raw-bucket",
        "PUBLIC_BUCKET": "public-bucket", "CITY_ID": "boston",
    }.items():  # fmt: skip
        mp.setenv(k, v)
    mp.setenv("HEADWAY_DUCKDB_PATH", str(tmp / "handler.duckdb"))
    clock = {"now": datetime(2026, 11, 22, 14, 20, tzinfo=UTC)}
    published: list[str] = []
    mp.setattr(handler, "_now", lambda: clock["now"])

    def fake_dbt(args: list[str]) -> handler.DbtOutcome:
        if args[0] == "run-operation":
            body = json.dumps({"row_counts": {"dim_route": 5}})
            s3.put_object(Bucket="public-bucket", Key=f"{GOLD}/manifest.json", Body=body)
            for table in publish.gold_tables():
                s3.put_object(Bucket="public-bucket", Key=f"{GOLD}/{table}.parquet", Body=b"x")
        return handler.DbtOutcome(True, 9, 0)

    def fake_publish(token: str, url: str, tables: list[str], **_: Any) -> list[str]:
        published.append(url)
        return tables

    mp.setattr(handler, "run_dbt", fake_dbt)
    mp.setattr(handler.publish, "publish_motherduck", fake_publish)
    mp.setattr(handler.cost, "write_cost_daily", lambda *a, **k: None)

    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    root = logging.getLogger()
    collector = Collect(level=logging.INFO)
    root.addHandler(collector)
    out: dict[str, Any] = {"published": published, "records": records}
    with mock_aws():
        s3 = boto3.client("s3")
        s3.create_bucket(Bucket="raw-bucket")
        s3.create_bucket(Bucket="public-bucket")
        ssm = boto3.client("ssm")
        ssm.put_parameter(Name=handler.TOKEN_PARAMETER, Value="md-secret", Type="SecureString")

        def invoke(at: datetime) -> dict[str, Any]:
            clock["now"] = at
            try:
                return handler.lambda_handler({}, None)
            except handler.ChaosError as exc:
                return {"raised": f"{type(exc).__name__}: {exc}"}

        out["healthy"] = invoke(datetime(2026, 11, 22, 14, 20, tzinfo=UTC))
        ssm.put_parameter(
            Name="/headway/chaos", Type="String",
            Value=json.dumps({"mode": "md_down", "until": DRILL_END}),
        )  # fmt: skip
        out["published_before_drill"] = len(published)
        out["drill"] = invoke(DRILL_START)
        out["published_in_drill"] = len(published) - out["published_before_drill"]
        out["gold_after_drill"] = sorted(
            o["Key"] for o in s3.list_objects_v2(Bucket="public-bucket", Prefix=GOLD)["Contents"]
        )
        out["recovered"] = invoke(datetime(2026, 11, 22, 16, 20, tzinfo=UTC))
        w = World(tmp)
        w.curate()
        state.load(s3, "raw-bucket", str(tmp / "world.duckdb"), state.TRANSFORM_RUNS, clock["now"])
        out["world"] = w
        out["result"] = w.build(slo_as_of=SLO_AS_OF)
    root.removeHandler(collector)
    mp.undo()
    return out


def test_the_drill_run_raises_after_gold_is_exported_and_never_calls_motherduck(
    world: dict[str, Any],
) -> None:
    assert "ChaosError" in world["drill"]["raised"] and "md_down" in world["drill"]["raised"]
    assert world["published_in_drill"] == 0
    assert f"{GOLD}/manifest.json" in world["gold_after_drill"]
    assert f"{GOLD}/dim_route.parquet" in world["gold_after_drill"]


def test_the_run_state_records_a_failed_run_that_exported_but_did_not_publish(
    world: dict[str, Any],
) -> None:
    rows = world["result"].rows(
        "select cast(slot as varchar) as slot, status, gold_exported_at is not null as exported, "
        "gold_published, error from stg_transform_runs order by started_at"
    )
    assert [(r["status"], r["exported"], r["gold_published"]) for r in rows] == [
        ("success", True, True), ("failed", True, False), ("success", True, True),
    ]  # fmt: skip
    assert "ChaosError" in rows[1]["error"]


def test_transform_success_counts_the_drill_slot_as_bad_and_recovers(world: dict[str, Any]) -> None:
    result = world["result"]
    slo = result.one(
        "select good_events, total_events, met, error_budget_used from agg_slo_daily "
        "where slo_name = 'transform_success' and service_date = date '2026-11-22'"
    )
    assert (slo["good_events"], slo["total_events"]) == (2, 3) and slo["met"] is False
    assert slo["error_budget_used"] > 1
    # the slot after the drill is good again
    good = {r["slot"] for r in result.rows(
        "select cast(slot as varchar) as slot from stg_transform_runs "
        "where status = 'success' and gold_published"
    )}  # fmt: skip
    assert good == {"2026-11-22 14:00:00+00", "2026-11-22 16:00:00+00"}


def test_gold_freshness_is_unaffected_because_gold_was_exported(world: dict[str, Any]) -> None:
    fresh = world["result"].one(
        "select good_events, total_events from agg_slo_daily "
        "where slo_name = 'gold_freshness' and service_date = date '2026-11-22'"
    )
    assert fresh["good_events"] == fresh["total_events"] > 0


def test_the_drill_is_logged_loudly_and_the_publish_error_is_in_the_run_summary(
    world: dict[str, Any],
) -> None:
    messages = [r.getMessage() for r in world["records"]]
    assert any("CHAOS ACTIVE mode=md_down" in m for m in messages)
    summaries = [json.loads(m) for m in messages if m.startswith("{") and "gold_exported" in m]
    assert len(summaries) == 3
    assert "publish_error" not in summaries[0] and "publish_error" not in summaries[2]
    assert "ChaosError" in summaries[1]["publish_error"] and summaries[1]["gold_exported"] is True
    assert any(r.levelno == logging.ERROR and "MotherDuck publish failed" in r.getMessage()
               for r in world["records"])  # fmt: skip
