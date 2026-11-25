"""The warm-container gold cache: hit, refresh on a new export, and fallback to remote reads."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_gold
from headway_api.app import create_app
from headway_api.cache import GoldSource
from headway_api.queries import API_TABLES
from headway_api.settings import Settings


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def remote(tmp_path: Path) -> tuple[str, Path]:
    pub = tmp_path / "pub"
    return api_gold.build(pub), pub


def source(remote_url: str, tmp_path: Path, clock: Clock) -> GoldSource:
    return GoldSource(remote_url, str(tmp_path / "cache"), API_TABLES, 60.0, clock)


def test_first_use_downloads_then_hits_the_local_copy(
    remote: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = remote
    clock = Clock()
    src = source(url, tmp_path, clock)
    reads: list[str] = []
    real = src._download

    def counting(manifest: dict[str, object], exported: str) -> str:
        reads.append(exported)
        return real(manifest, exported)

    monkeypatch.setattr(src, "_download", counting)
    snap = src.snapshot()
    assert snap.served_from == "cache" and snap.base.startswith(f"file://{tmp_path}/cache/")
    assert (tmp_path / "cache" / "2026-11-23T000000Z" / "gold" / "agg_slo_rollup.parquet").exists()
    clock.t += 30  # inside the re-check window: no manifest read at all
    (tmp_path / "pub" / "gold" / "manifest.json").unlink()
    assert src.snapshot() is snap
    clock.t += 31  # re-check: the manifest is unreadable, the last export keeps being served
    assert src.snapshot().served_from == "cache"
    assert reads == ["2026-11-23T00:00:00Z"]


def test_unchanged_export_is_not_downloaded_again(
    remote: tuple[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = remote
    clock = Clock()
    src = source(url, tmp_path, clock)
    src.snapshot()
    clock.t += 61
    monkeypatch.setattr(src, "_download", lambda m, e: pytest.fail("downloaded again"))
    assert src.snapshot().served_from == "cache"


def test_refresh_on_a_new_exported_at(remote: tuple[str, Path], tmp_path: Path) -> None:
    url, pub = remote
    clock = Clock()
    settings = Settings(data_url=url, cache_dir=str(tmp_path / "cache"), recheck_s=60.0)
    client = TestClient(create_app(settings, source(url, tmp_path, clock)))

    def median() -> float:
        body = client.get("/v1/lines/Red/reliability").json()
        assert body["provenance"]["served_from"] == "cache"
        return body["data"]["predicted_deviation"]["rows"][0]["median_predicted_deviation_s"]  # type: ignore[no-any-return]

    assert median() == 60.0
    api_gold.build(pub, exported_at="2026-11-23T01:00:00Z", shift=15.0)
    assert median() == 60.0  # not re-checked yet: the export is the same for up to 60 s
    clock.t += 61
    assert median() == 75.0
    assert sorted(os.listdir(tmp_path / "cache")) == ["2026-11-23T010000Z"]  # the old one is gone
    # the provenance SQL names the public objects, not the local copy
    sql = client.get("/v1/lines/Red/reliability").json()["provenance"]["sql"][0]
    assert str(tmp_path / "cache") not in sql and str(tmp_path / "pub") in sql


def test_download_failure_falls_back_to_remote_reads(
    remote: tuple[str, Path], tmp_path: Path
) -> None:
    url, _ = remote
    blocker = tmp_path / "cache"
    blocker.write_text("a file where the cache directory should be")
    src = source(url, tmp_path, Clock())
    snap = src.snapshot()
    assert snap.served_from == "remote" and snap.base == url
    client = TestClient(create_app(Settings(data_url=url, cache_dir=str(blocker)), src))
    res = client.get("/v1/slo")
    assert res.status_code == 200 and res.json()["provenance"]["served_from"] == "remote"


def test_a_broken_local_copy_falls_back_for_that_request(
    remote: tuple[str, Path], tmp_path: Path
) -> None:
    url, _ = remote
    clock = Clock()
    src = source(url, tmp_path, clock)
    client = TestClient(create_app(Settings(data_url=url, cache_dir=str(tmp_path / "cache")), src))
    assert client.get("/v1/slo").json()["provenance"]["served_from"] == "cache"
    (tmp_path / "cache" / "2026-11-23T000000Z" / "gold" / "agg_slo_rollup.parquet").unlink()
    res = client.get("/v1/slo")
    assert res.status_code == 200 and res.json()["provenance"]["served_from"] == "remote"
    assert res.json()["data"]["slos"][0]["slo_name"] == "transform_success"
    assert client.get("/v1/slo").json()["provenance"]["served_from"] == "remote"  # until re-check
    clock.t += 61  # the next re-check restores the copy for this export
    assert client.get("/v1/slo").json()["provenance"]["served_from"] == "cache"


def test_manifest_unreadable_on_first_use_is_an_error(tmp_path: Path) -> None:
    from headway_mcp import runner

    src = GoldSource(f"file://{tmp_path}/none", str(tmp_path / "c"), API_TABLES)
    with pytest.raises(runner.DataError):
        src.snapshot()
