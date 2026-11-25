"""The public API over a local file:// gold fixture: no network."""

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api_gold
from headway_api.app import create_app
from headway_api.settings import Settings

ORIGIN = "https://headway.example.vercel.app"


@pytest.fixture(scope="module")
def root(tmp_path_factory: pytest.TempPathFactory) -> str:
    return api_gold.build(tmp_path_factory.mktemp("api_export"))


def make(root: str, **kw: Any) -> TestClient:
    return TestClient(create_app(Settings(data_url=root, **kw)))


@pytest.fixture
def client(root: str) -> TestClient:
    return make(root)


def rows(body: dict[str, Any], section: str) -> list[dict[str, Any]]:
    return body["data"][section]["rows"]  # type: ignore[no-any-return]


def test_every_response_has_coverage_freshness_provenance(client: TestClient) -> None:
    for path in ("/v1/lines", "/v1/lines/Red/reliability", "/v1/lines/Red/accuracy",
                 "/v1/lines/Red/headways", "/v1/lines/Red/countdown", "/v1/incidents",
                 "/v1/forecast/Red", "/v1/pipeline/health", "/v1/slo", "/v1/metrics",
                 "/v1/meta"):  # fmt: skip
        res = client.get(path)
        assert res.status_code == 200, path
        body = res.json()
        assert {"data", "coverage", "freshness", "provenance"} <= set(body), path
        assert body["freshness"]["exported_at"] == api_gold.EXPORTED_AT
        assert body["freshness"]["metrics_as_of"].startswith("2026-11-22")
        assert body["provenance"]["metrics_md"].startswith("docs/METRICS.md#")
        assert body["coverage"]["rule"]


def test_provenance_names_tables_and_sql(client: TestClient) -> None:
    prov = client.get("/v1/lines/Red/reliability").json()["provenance"]
    assert prov["tables"] == ["agg_line_reliability_window", "agg_actual_deviation_window"]
    assert all(s.startswith("select") and "'Red'" in s for s in prov["sql"])
    assert prov["row_limit"] == 500 and prov["truncated"] is False
    assert prov["served_from"] == "remote"


def test_reliability_values_and_window(client: TestClient) -> None:
    body = client.get("/v1/lines/Red/reliability?window=last_7d").json()
    assert body["data"]["window"] == "last_7d"
    assert rows(body, "predicted_deviation") == []  # that table only has last_24h
    assert rows(body, "actual_deviation")[0]["median_deviation_s"] == 30.0
    body = client.get("/v1/lines/Red/reliability").json()
    row = rows(body, "predicted_deviation")[0]
    assert row["median_predicted_deviation_s"] == 60.0 and row["withheld_reason"] is None


def test_low_coverage_values_are_null_with_a_reason(client: TestClient) -> None:
    body = client.get("/v1/lines/Blue/reliability").json()
    row = rows(body, "predicted_deviation")[0]
    assert row["median_predicted_deviation_s"] is None
    assert row["coverage_pct"] == 30.0
    assert row["withheld_reason"] == "coverage 30% is below 50%"
    assert body["coverage"]["withheld_rows"] == 2
    for ep, sec in (("accuracy", "prediction_accuracy"), ("headways", "headway_adherence")):
        r = rows(client.get(f"/v1/lines/Blue/{ep}").json(), sec)[0]
        assert r["withheld_reason"] and [v for k, v in r.items() if k.startswith("pct_")][0] is None


def test_countdown_rows_per_station(client: TestClient) -> None:
    got = rows(client.get("/v1/lines/Red/countdown").json(), "countdown_accuracy")
    assert [r["station_id"] for r in got] == ["place-a", "place-b"]
    assert got[0]["pct_within_60s"] == 50.0


def test_lines_lists_all_lines(client: TestClient) -> None:
    data = client.get("/v1/lines").json()["data"]
    assert [d["line"] for d in data][:3] == ["Red", "Orange", "Blue"] and len(data) == 8
    assert data[2]["withheld_last_24h"] is True and data[0]["withheld_last_24h"] is False
    assert data[3]["withheld_reason"] == "no row for this line"


def test_incidents_range_line_and_privacy(client: TestClient) -> None:
    body = client.get("/v1/incidents?line=Red&from=2026-11-20&to=2026-11-22").json()
    inc = body["data"]["incidents"]
    assert [i["incident_id"] for i in inc] == ["inc-1"] and inc[0]["thresholds_crossed"]
    assert "evidence_json" not in inc[0]
    assert client.get("/v1/incidents?line=Orange").json()["data"]["incidents"] == []
    assert (
        client.get("/v1/incidents?from=2026-11-01&to=2026-11-05").json()["data"]["incidents"] == []
    )


def test_forecast_withholds_methods_below_half_coverage(client: TestClient) -> None:
    data = client.get("/v1/forecast/Red").json()["data"]
    h = data["horizons"][0]
    assert data["status"] == "baselines_only" and data["training_gate"][0]["gate_passed"] is False
    assert h["persistence"] == 30.0 and h["model_forecast_s"] is None
    assert "model_forecast_s" in h["withheld_reasons"]
    assert h["daytype_hour"] == 20.0 and h["daytype_hour_coverage_pct"] == 50.0  # 4 of 8 trips


def test_slo_health_metrics_meta(client: TestClient) -> None:
    assert client.get("/v1/slo").json()["data"]["slos"][0]["slo_name"] == "transform_success"
    assert client.get("/v1/pipeline/health?days=3").json()["data"]["daily"][0]["runs"] == 1
    metrics = client.get("/v1/metrics").json()["data"]
    assert any(m["name"] == "on_time_pct" and "#7-actual" in m["metrics_md"] for m in metrics)
    meta = client.get("/v1/meta").json()["data"]
    assert meta["coverage_rules"]["min_coverage_pct"] == 50.0
    assert "agg_slo_rollup" in meta["tables_in_export"] and meta["limits"]["max_rows"] == 500


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/v1/lines/Purple/reliability", "Red"),
        ("/v1/lines/Red/reliability?window=last_year", "last_7d"),
        ("/v1/forecast/Nope", "Mattapan"),
        ("/v1/incidents?line=Nope", "Blue"),
        ("/v1/incidents?from=yesterday", None),
        ("/v1/incidents?from=2026-01-01&to=2026-11-22", None),
        ("/v1/pipeline/health?days=99", None),
        ("/v1/pipeline/health?days=abc", None),
    ],
)
def test_validation_errors_are_422_with_allowed_values(
    client: TestClient, path: str, allowed: str | None
) -> None:
    res = client.get(path)
    assert res.status_code == 422 and res.headers["cache-control"] == "no-store"
    detail = res.json()["detail"]
    assert res.json()["error"] == "validation_error" and detail
    if allowed:
        assert allowed in detail[0]["allowed"]


def test_cache_control_matches_gold_freshness(root: str) -> None:
    res = make(root, cache_max_age_s=300).get("/v1/slo")
    assert res.headers["cache-control"] == "public, max-age=300"
    assert make(root, cache_max_age_s=60).get("/v1/slo").headers["cache-control"] == (
        "public, max-age=60"
    )
    assert make(root).get("/v1/nothing").headers["cache-control"] == "no-store"


def test_cors_allowlist(client: TestClient) -> None:
    ok = client.get("/v1/slo", headers={"Origin": ORIGIN})
    assert ok.headers["access-control-allow-origin"] == ORIGIN
    bad = client.get("/v1/slo", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in bad.headers
    pre = client.options(
        "/v1/slo", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"}
    )
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" not in pre.headers["access-control-allow-methods"]


def test_wildcard_origin_is_dropped(monkeypatch: pytest.MonkeyPatch, root: str) -> None:
    monkeypatch.setenv("API_CORS_ORIGINS", f"*, {ORIGIN}")
    monkeypatch.setenv("HEADWAY_DATA_URL", root)
    assert Settings.from_env().cors_origins == (ORIGIN,)


def test_request_size_and_row_limits(root: str) -> None:
    c = make(root, max_request_bytes=300)
    assert c.get("/v1/slo", headers={"content-length": "301"}).status_code == 413
    assert c.get("/v1/incidents?line=Red&x=" + "a" * 400).status_code == 413
    limited = make(root, max_rows=1).get("/v1/lines/Red/countdown").json()
    assert len(rows(limited, "countdown_accuracy")) == 1
    assert limited["provenance"]["truncated"] is True and limited["provenance"]["row_limit"] == 1


def test_missing_table_is_404_not_available(tmp_path: Path) -> None:
    base = tmp_path / "gold"
    base.mkdir()
    manifest = {"exported_at": "x", "metrics_as_of": "2026-11-22T15:00:00Z", "row_counts": {}}
    (base / "manifest.json").write_text(json.dumps(manifest))
    res = make(f"file://{tmp_path}").get("/v1/forecast/Red")
    assert res.status_code == 404 and res.json()["error"] == "not_available"
    assert make(f"file://{tmp_path}").get("/v1/lines/Red/reliability").status_code == 404


def test_unreadable_export_is_502(tmp_path: Path) -> None:
    res = make(f"file://{tmp_path}/none").get("/v1/slo")
    assert res.status_code == 502 and res.json()["error"] == "data_unavailable"


def test_docs_and_openapi(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert "/v1/lines/{line}/reliability" in spec["paths"] and "/v1/meta" in spec["paths"]
    assert all(p.startswith(("/v1", "/docs")) or p == "/openapi.json" for p in spec["paths"])


def test_structured_access_log(client: TestClient, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="headway_api.access"):
        client.get("/v1/lines/Red/reliability")
    line = json.loads(next(r.message for r in caplog.records if r.name == "headway_api.access"))
    assert line["status"] == 200 and line["path"] == "/v1/lines/Red/reliability"
    assert line["rows"] == 2 and line["latency_ms"] >= 0


def test_sql_reads_only_allowlisted_gold_tables(client: TestClient) -> None:
    import re

    from headway_api.queries import API_TABLES

    for path in ("/v1/lines/Red/reliability", "/v1/lines/Red/accuracy", "/v1/lines/Red/headways",
                 "/v1/lines/Red/countdown", "/v1/incidents", "/v1/forecast/Red",
                 "/v1/pipeline/health", "/v1/slo"):  # fmt: skip
        for sql in client.get(path).json()["provenance"]["sql"]:
            for rel in re.findall(r"read_parquet\('([^']+)'", sql):
                assert re.search(r"/gold/([a-z_]+)\.parquet$", rel), rel
                assert re.search(r"/gold/([a-z_]+)\.parquet$", rel).group(1) in API_TABLES  # type: ignore[union-attr]
            assert "curated" not in sql and "raw" not in sql.split("from")[1:][0:1].__str__()


def test_lambda_handler_with_an_http_api_event(root: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HEADWAY_DATA_URL", root)
    from mangum import Mangum

    handler = Mangum(create_app(Settings.from_env()), lifespan="off")
    event = {
        "version": "2.0", "routeKey": "$default", "rawPath": "/v1/slo", "rawQueryString": "",
        "headers": {"host": "x.execute-api.us-east-1.amazonaws.com", "origin": ORIGIN},
        "requestContext": {
            "http": {"method": "GET", "path": "/v1/slo", "protocol": "HTTP/1.1",
                     "sourceIp": "203.0.113.1", "userAgent": "t"},
            "stage": "$default",
        },
        "isBase64Encoded": False,
    }  # fmt: skip
    res = handler(event, object())  # type: ignore[arg-type]
    assert res["statusCode"] == 200
    assert res["headers"]["cache-control"] == "public, max-age=300"
    assert json.loads(res["body"])["data"]["slos"][0]["slo_name"] == "transform_success"
