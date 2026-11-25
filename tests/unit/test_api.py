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
                 "/v1/meta", "/v1/lines/Red/segments", "/v1/lines/Red/marey",
                 "/v1/pipeline/heartbeat", "/v1/lines/Red/trip-time",
                 "/v1/lines/Red/spacing-by-hour", "/v1/lines/Red/disruptions",
                 "/v1/pipeline/checks"):  # fmt: skip
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
        ("/v1/lines/Red/segments?direction=2", None),
        ("/v1/lines/Red/segments?window=last_year", "last_7d"),
        ("/v1/lines/Red/marey?direction=x", None),
        ("/v1/pipeline/heartbeat?hours=0", None),
        ("/v1/pipeline/heartbeat?hours=9999", None),
        ("/v1/lines/Red/trip-time?window=last_year", "last_7d"),
        ("/v1/lines/Red/spacing-by-hour?date=yesterday", None),
        ("/v1/pipeline/checks?hours=0", None),
        ("/v1/pipeline/checks?hours=9999", None),
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


SEGMENT_KEYS = {
    "direction_id", "from_stop_id", "from_stop_name", "to_stop_id", "to_stop_name",
    "segment_order", "health", "pct_within_band", "pct_bunched", "pct_gap", "n_headways",
    "n_excluded_outage", "disrupted", "coverage_pct", "withheld_reason", "disruption_header",
}  # fmt: skip


def test_segments_shape_health_and_disruption(client: TestClient) -> None:
    body = client.get("/v1/lines/Red/segments").json()
    assert body["data"]["line"] == "Red" and body["data"]["window"] == "last_24h"
    segs = body["data"]["rows"]
    assert [s["segment_order"] for s in segs] == [1, 2, 3, 1]
    assert all(set(s) == SEGMENT_KEYS for s in segs)
    assert [s["health"] for s in segs] == ["even", "some_bunching", "bunched_gaps", "even"]
    assert segs[0]["from_stop_name"] == "Alpha" and segs[0]["pct_within_band"] == 85.0
    assert [s["disrupted"] for s in segs] == [False, True, False, False]
    assert segs[3]["n_excluded_outage"] == 3 and segs[3]["withheld_reason"] is None
    # the header of the disruption that touches a disrupted segment (public MBTA text); none on a
    # segment that is not disrupted, even when an older alert listed it (a0 on a1-b1)
    assert [s["disruption_header"] for s in segs] == [
        None, "Shuttle buses replace trains between Beta and Gamma", None, None]  # fmt: skip
    assert body["provenance"]["tables"] == ["agg_headway_segment_window", "agg_service_disruptions"]


def test_segments_withhold_below_half_coverage_and_report_partial(client: TestClient) -> None:
    body = client.get("/v1/lines/Red/segments?direction=0").json()
    shown = body["data"]["rows"][2]  # 55% coverage: shown (partial), not withheld
    assert shown["withheld_reason"] is None and shown["health"] == "bunched_gaps"
    blue = client.get("/v1/lines/Blue/segments").json()
    seg = blue["data"]["rows"][0]
    assert seg["coverage_pct"] == 30.0 and seg["n_headways"] == 3
    assert seg["health"] is None and seg["pct_within_band"] is None
    assert seg["pct_bunched"] is None and seg["pct_gap"] is None
    assert "below 50%" in seg["withheld_reason"]
    assert blue["coverage"]["withheld_rows"] == 1 and blue["coverage"]["min_coverage_pct"] == 30.0


def test_segments_direction_filter_and_empty_window(client: TestClient) -> None:
    only1 = client.get("/v1/lines/Red/segments?direction=1").json()["data"]
    assert only1["direction"] == 1 and [s["direction_id"] for s in only1["rows"]] == [1]
    assert client.get("/v1/lines/Red/segments?window=last_7d").json()["data"]["rows"] == []


def test_marey_rows_hour_and_filter(client: TestClient) -> None:
    data = client.get("/v1/lines/Red/marey").json()["data"]
    assert data["hour_start"] == "2026-11-22T14:00:00Z" and data["direction"] is None
    assert [(r["trip_id"], r["stop_sequence"], r["method"]) for r in data["rows"]] == [
        ("t1", 1, "observed"),
        ("t1", 2, "estimated"),
        ("t2", 1, "observed"),
    ]
    first = data["rows"][0]
    assert set(first) == {"direction_id", "trip_id", "stop_id", "stop_name", "stop_sequence",
                          "arrival_ts", "method", "confidence", "scheduled_ts"}  # fmt: skip
    assert first["arrival_ts"] == "2026-11-22T14:10:00Z"
    assert first["scheduled_ts"] == "2026-11-22T14:09:00Z"
    d1 = client.get("/v1/lines/Red/marey?direction=1").json()["data"]
    assert [r["trip_id"] for r in d1["rows"]] == ["t2"]
    assert client.get("/v1/lines/Blue/marey").json()["data"]["rows"] == []


def test_heartbeat_hours_and_outage_flag(client: TestClient) -> None:
    data = client.get("/v1/pipeline/heartbeat").json()["data"]
    assert data["hours"] == 24 and len(data["rows"]) == 3
    assert data["rows"][0] == {
        "hour_start": "2026-11-22T12:00:00Z", "expected_runs": 60, "missed_runs": 0,
        "feed_gap_min": 0.0, "data_outage": False,
    }  # fmt: skip
    assert [r["data_outage"] for r in data["rows"]] == [False, False, True]
    last = client.get("/v1/pipeline/heartbeat?hours=1").json()["data"]["rows"]
    assert [r["hour_start"] for r in last] == ["2026-11-22T14:00:00Z"]
    assert last[0]["missed_runs"] == 22 and last[0]["feed_gap_min"] == 18.0


def test_r4_endpoints_are_404_when_the_export_lacks_the_tables(root: str, tmp_path: Path) -> None:
    import shutil

    shutil.copytree(root.removeprefix("file://"), tmp_path / "x")
    gold = tmp_path / "x" / "gold"
    m = json.loads((gold / "manifest.json").read_text())
    r4 = ("agg_headway_segment_window", "agg_marey_last_hour", "agg_capture_heartbeat_hourly",
          "agg_ett_band_window", "agg_service_disruptions")  # fmt: skip
    for t in r4:
        m["row_counts"].pop(t)
    (gold / "manifest.json").write_text(json.dumps(m))
    c = make(f"file://{tmp_path / 'x'}")
    for path in (
        "/v1/lines/Red/segments",
        "/v1/lines/Red/marey",
        "/v1/pipeline/heartbeat",
        "/v1/lines/Red/trip-time",
        "/v1/lines/Red/disruptions",
    ):
        assert c.get(path).status_code == 404, path
    # spacing-by-hour and the checks read R2 and base tables: still served
    assert c.get("/v1/lines/Red/spacing-by-hour").status_code == 200
    assert c.get("/v1/pipeline/checks").status_code == 200
    assert c.get("/v1/lines/Red/headways").status_code == 200


TRIP_TIME_KEYS = {
    "direction_id", "time_band", "n_trips", "n_scheduled_trips", "n_excluded_outage",
    "n_implausible", "n_disruption", "median_ett_s", "p90_ett_s", "median_scheduled_s",
    "median_actual_s", "coverage_pct", "status", "withheld_reason", "no_trips_reason",
}  # fmt: skip


def test_trip_time_bands_zero_trips_and_withheld(client: TestClient) -> None:
    body = client.get("/v1/lines/Red/trip-time").json()
    data = body["data"]
    assert data["line"] == "Red" and data["window"] == "last_24h" and data["unit"] == "seconds"
    got = data["rows"]
    assert all(set(r) == TRIP_TIME_KEYS for r in got)
    assert [(r["direction_id"], r["time_band"], r["status"]) for r in got] == [
        (0, "am_peak", "measured"), (0, "midday", "no_trips"), (0, "evening", "no_trips"),
        (1, "pm_peak", "withheld")]  # fmt: skip
    am, midday, evening, pm = got
    assert (am["n_trips"], am["median_ett_s"], am["p90_ett_s"]) == (8, 90.0, 150.0)
    assert (am["median_scheduled_s"], am["median_actual_s"]) == (1800.0, 1890.0)
    assert am["coverage_pct"] == 80.0 and am["withheld_reason"] is None
    # zero trips is not a withheld row: n_trips is 0, no value, its own reason
    assert midday["n_trips"] == 0 and midday["median_ett_s"] is None
    assert (
        midday["withheld_reason"] is None and "none with a usable ETT" in midday["no_trips_reason"]
    )
    assert evening["n_scheduled_trips"] == 0 and "data-outage" in evening["no_trips_reason"]
    # withheld: trips exist but coverage is 40%
    assert pm["n_trips"] == 2 and pm["median_ett_s"] is None and pm["p90_ett_s"] is None
    assert "below 50%" in pm["withheld_reason"] and pm["no_trips_reason"] is None
    assert data["overall"]["n_trips"] == 10 and data["overall"]["median_ett_s"] == 80.0
    assert data["overall"]["status"] == "measured"
    assert body["provenance"]["tables"] == ["agg_ett_band_window", "agg_ett_window"]
    assert body["coverage"]["withheld_rows"] == 1


def test_trip_time_line_without_rows_and_overall_zero(client: TestClient) -> None:
    assert client.get("/v1/lines/Red/trip-time?window=last_7d").json()["data"]["rows"] == []
    blue = client.get("/v1/lines/Blue/trip-time").json()["data"]
    assert blue["rows"] == [] and blue["overall"]["status"] == "no_trips"
    assert blue["overall"]["n_trips"] == 0
    orange = client.get("/v1/lines/Orange/trip-time").json()["data"]["rows"]
    assert orange[0]["median_ett_s"] == -20.0 and orange[0]["status"] == "measured"


def test_spacing_by_hour_order_outage_and_states(client: TestClient) -> None:
    body = client.get("/v1/lines/Red/spacing-by-hour").json()
    data = body["data"]
    assert data["line"] == "Red" and data["service_date"] == "2026-11-22"
    hours = data["hours"]
    assert [h["hour_local"] for h in hours] == [5, 6, 7, 8, 1]  # 05:00 first, 01:00 last
    assert [h["hour_start"] for h in hours] == [
        "2026-11-22T10:00:00Z", "2026-11-22T11:00:00Z", "2026-11-22T12:00:00Z",
        "2026-11-22T13:00:00Z", "2026-11-23T06:00:00Z"]  # fmt: skip
    assert [h["status"] for h in hours] == ["measured", "measured", "outage", "no_headways",
                                            "withheld"]  # fmt: skip
    assert set(hours[0]) == {"service_date", "hour_local", "hour_start", "n_headways",
                             "pct_within_band", "pct_bunched", "pct_gap", "scheduled_headways",
                             "coverage_pct", "data_outage", "status",
                             "withheld_reason"}  # fmt: skip
    first = hours[0]  # both directions combined, weighted by headways: (8*75 + 2*100) / 10
    assert first["n_headways"] == 10 and first["pct_within_band"] == 80.0
    assert first["pct_bunched"] == 10.0 and first["pct_gap"] == 10.0
    assert first["scheduled_headways"] == 12 and first["coverage_pct"] == 83.3
    outage = hours[2]
    assert outage["data_outage"] is True and outage["n_headways"] == 0
    assert outage["pct_within_band"] is None and "data outage" in outage["withheld_reason"]
    assert hours[3]["n_headways"] == 0 and hours[3]["data_outage"] is False
    assert hours[3]["withheld_reason"] is None
    assert hours[4]["pct_within_band"] is None and "below 50%" in hours[4]["withheld_reason"]
    assert body["coverage"]["withheld_rows"] == 1  # the outage hour is not counted as coverage


def test_spacing_by_hour_date_and_empty(client: TestClient) -> None:
    prev = client.get("/v1/lines/Red/spacing-by-hour?date=2026-11-21").json()["data"]
    assert prev["service_date"] == "2026-11-21" and [h["hour_local"] for h in prev["hours"]] == [6]
    none = client.get("/v1/lines/Blue/spacing-by-hour?date=2026-11-20").json()["data"]
    assert none["hours"] == [] and none["service_date"] == "2026-11-20"


def test_disruptions_lists_active_first_with_segments(client: TestClient) -> None:
    data = client.get("/v1/lines/Red/disruptions").json()["data"]
    assert data["line"] == "Red" and data["window"] == "last_24h"
    first, second = data["rows"]
    assert set(first) == {"alert_id", "effect", "header_text", "first_seen", "last_seen",
                          "is_active", "n_stations", "from_station_name", "to_station_name",
                          "segments"}  # fmt: skip
    assert first["alert_id"] == "a1" and first["is_active"] is True
    assert first["header_text"] == "Shuttle buses replace trains between Beta and Gamma"
    assert (
        first["first_seen"] == "2026-11-22T14:00:00Z"
        and first["last_seen"] == "2026-11-22T14:40:00Z"
    )
    assert (first["from_station_name"], first["to_station_name"]) == ("Beta", "Gamma")
    assert first["segments"] == [{"direction_id": 0, "from_stop_id": "b1", "to_stop_id": "c1"},
                                 {"direction_id": 1, "from_stop_id": "d2",
                                  "to_stop_id": "c2"}]  # fmt: skip
    assert second["alert_id"] == "a0" and second["is_active"] is False
    assert client.get("/v1/lines/Blue/disruptions").json()["data"]["rows"] == []
    assert "evidence" not in json.dumps(client.get("/v1/lines/Red/disruptions").json())


def test_checks_counts_fail_and_warn_hours_per_check(client: TestClient) -> None:
    body = client.get("/v1/pipeline/checks").json()
    data = body["data"]
    assert data["hours"] == 24 and data["n_checks"] == 6
    assert data["n_failing_checks"] == 1 and data["n_warning_checks"] == 1
    by = {c["check_name"]: c for c in data["checks"]}
    assert list(by) == sorted(by)
    assert set(by["missed_runs"]) == {"check_name", "n_hours_evaluated", "n_fail_hours",
                                      "n_warn_hours", "n_ok_hours", "worst_status"}  # fmt: skip
    assert (by["missed_runs"]["n_fail_hours"], by["missed_runs"]["n_warn_hours"],
            by["missed_runs"]["n_ok_hours"], by["missed_runs"]["worst_status"]) == (
        1, 1, 1, "fail")  # fmt: skip
    # the fail two days ago is outside the window
    assert by["feed_gap_min"]["worst_status"] == "ok" and by["feed_gap_min"]["n_fail_hours"] == 0
    # no_service hours are left out: one warn hour, one no_service hour not counted
    pc = by["prediction_coverage"]
    assert (pc["n_hours_evaluated"], pc["n_warn_hours"], pc["worst_status"]) == (1, 1, "warn")
    # a check with only no_service / no_baseline hours is not evaluated
    assert by["late_event_share"]["worst_status"] == "not_evaluated"
    assert by["late_event_share"]["n_hours_evaluated"] == 0
    assert by["vehicle_count_drop"]["worst_status"] == "not_evaluated"
    assert by["duplicate_share"]["worst_status"] == "ok"
    wide = client.get("/v1/pipeline/checks?hours=720").json()["data"]
    assert wide["n_failing_checks"] == 2  # feed_gap_min failed two days ago
    assert body["provenance"]["tables"] == ["agg_quality_checks_hourly"]


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
                 "/v1/pipeline/health", "/v1/slo", "/v1/lines/Red/segments", "/v1/lines/Red/marey",
                 "/v1/pipeline/heartbeat", "/v1/lines/Red/trip-time",
                 "/v1/lines/Red/spacing-by-hour", "/v1/lines/Red/disruptions",
                 "/v1/pipeline/checks"):  # fmt: skip
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
