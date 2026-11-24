"""Simulation 3: the scheduler delivers a minute twice. One manifest wins, the second delivery
leaves a duplicate record, and nothing is counted twice."""

import json

import pytest

from .world import CITY, World, at

SLO_AS_OF = "2026-11-22T16:59:30Z"
# A minute ingest wrote itself (so it was claimed with a conditional create), late enough that
# one-shot curation still lists its duplicate record: curation re-lists records 2 hours back.
MINUTE = at(15, 50)


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> World:
    world = World(tmp_path_factory.mktemp("duplicate_delivery"))
    assert world.claimed(MINUTE)
    world.second_delivery = world.run(MINUTE)  # type: ignore[attr-defined]
    world.curate()
    world.result = world.build(slo_as_of=SLO_AS_OF)  # type: ignore[attr-defined]
    return world


def test_one_manifest_and_one_duplicate_record(built: World) -> None:
    keys = built.manifest_keys(MINUTE)
    claimed = [k for k in keys if "/duplicates/" not in k]
    records = [k for k in keys if "/duplicates/" in k]
    assert len(claimed) == 1 and len(records) == 1
    record = json.loads(built.store.get_bytes(records[0]))
    assert record["duplicate_of"] == claimed[0]
    assert built.second_delivery["duplicate_delivery"] is True  # type: ignore[attr-defined]
    assert built.second_delivery["duplicate_of"] == claimed[0]  # type: ignore[attr-defined]
    assert built.second_delivery["duplicate_record"] == records[0]  # type: ignore[attr-defined]


def test_the_duplicate_is_flagged_and_warns(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    assert result.check("duplicate_deliveries", 10) == {"value": 1.0, "status": "warn"}
    run = result.one(
        "select duplicate_delivery_count as n from agg_pipeline_health "
        "where scheduled_at = timestamptz '2026-11-22 15:50:00+00'"
    )
    assert run["n"] == 1


def test_nothing_is_counted_twice(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    captures = result.one(
        "select count(*) as n, count(distinct capture_id) as ids from int_captures "
        "where capture_minute = timestamptz '2026-11-22 15:50:00+00'"
    )
    assert captures["n"] == captures["ids"] == 3  # the winner's three feeds, not six
    runs = result.one(
        "select count(*) as n from agg_pipeline_health "
        "where scheduled_at = timestamptz '2026-11-22 15:50:00+00'"
    )
    assert runs["n"] == 1
    capture_slo = result.one(
        "select good_events, total_events from agg_slo_daily "
        "where slo_name = 'capture_completeness' and service_date = date '2026-11-22'"
    )
    assert capture_slo["good_events"] == capture_slo["total_events"] == 180


def test_a_duplicate_is_not_an_outage_and_the_other_hours_are_clean(built: World) -> None:
    result = built.result  # type: ignore[attr-defined]
    for hour in (9, 10, 11):
        assert result.flags(hour)["data_outage"] is False
    assert result.check("duplicate_deliveries", 9) == {"value": 0.0, "status": "ok"}


def test_the_record_lives_under_the_duplicates_prefix(built: World) -> None:
    prefix = f"manifests/{CITY}/duplicates/"
    assert any(k.startswith(prefix) for k in built.store.list_keys(prefix))
