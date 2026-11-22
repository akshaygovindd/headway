import json
from pathlib import Path

import pytest

from headway.config import load_city_config
from headway.curate.replay import replay, summarize
from headway.storage import LocalStorage

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "boston"
CONFIG = load_city_config("boston")
EXPECTED = json.loads((SAMPLE / "EXPECTED.json").read_text())["curated"]
LAYERS = ("curated/", "quarantine/", "state/")


def snapshot(target: Path) -> dict[str, bytes]:
    storage = LocalStorage(target)
    return {k: storage.get_bytes(k) for layer in LAYERS for k in storage.list_keys(layer)}


@pytest.fixture(scope="module")
def replayed(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("replay")
    replay(SAMPLE, target, CONFIG, compact=True)
    return target


def test_curated_rows_match_expected(replayed: Path) -> None:
    summary = summarize(LocalStorage(replayed), CONFIG)
    assert summary.curated == EXPECTED["rows_per_table"]


def test_quarantine_counts_by_reason_match_expected(replayed: Path) -> None:
    summary = summarize(LocalStorage(replayed), CONFIG)
    expected = {t: r for t, r in EXPECTED["quarantine_by_reason"].items() if r}
    assert summary.quarantine == expected


def test_out_of_scope_rows_are_filtered_not_quarantined(tmp_path: Path) -> None:
    summary, _ = replay(SAMPLE, tmp_path, CONFIG, compact=False)
    assert summary.filtered_out_of_scope == EXPECTED["filtered_out_of_scope"]
    assert "unknown_route" not in {r for by in summary.quarantine.values() for r in by}


def test_every_hour_is_compacted(replayed: Path) -> None:
    keys = LocalStorage(replayed).list_keys("curated/boston/table=trip_updates/")
    assert keys and all(k.endswith("part-compacted.parquet") for k in keys)


def test_second_replay_into_same_target_changes_nothing(replayed: Path, tmp_path: Path) -> None:
    target = tmp_path / "same"
    replay(SAMPLE, target, CONFIG, compact=True)
    first = snapshot(target)
    replay(SAMPLE, target, CONFIG, compact=True)
    assert snapshot(target) == first
    assert summarize(LocalStorage(target), CONFIG).curated == EXPECTED["rows_per_table"]


def test_replay_into_fresh_target_is_byte_identical(replayed: Path, tmp_path: Path) -> None:
    replay(SAMPLE, tmp_path / "again", CONFIG, compact=True)
    assert snapshot(tmp_path / "again") == snapshot(replayed)


def test_uncompacted_replay_has_same_rows(tmp_path: Path) -> None:
    replay(SAMPLE, tmp_path, CONFIG, compact=False)
    summary = summarize(LocalStorage(tmp_path), CONFIG)
    assert summary.curated == EXPECTED["rows_per_table"]
