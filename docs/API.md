# Headway metric API (Release 4, Phase 4.1)

A public, read-only JSON API over the public gold tables. Built and tested; not deployed until
`enable_r4` is switched on ([DEPLOY.md](DEPLOY.md#release-4-switch-public-metric-api-off-by-default),
design in ADR-023). Interactive docs at `/docs`, schema at `/openapi.json`. No authentication: the
data is already public. Examples use `$API` for the base URL (`terraform output -raw api_url`).

## Rules every response follows

```json
{
  "data": {},
  "coverage":   {"rows": 2, "mean_coverage_pct": 60.0, "min_coverage_pct": 30.0,
                 "withheld_rows": 1, "rule": "A value from a row whose coverage is under 50% ..."},
  "freshness":  {"exported_at": "2026-11-23T00:00:00Z", "metrics_as_of": "2026-11-22T15:00:00Z",
                 "age_minutes": 42.0},
  "provenance": {"tables": ["agg_line_reliability_window"], "definition": "...",
                 "metrics_md": "docs/METRICS.md#1-predicted-schedule-deviation",
                 "row_limit": 500, "truncated": false, "served_from": "cache", "sql": ["select ..."]}
}
```

- **Withholding:** a value from a row whose coverage is under 50% is `null`, with `withheld_reason`
  (`"coverage 30% is below 50%"`) on the row; forecasts carry `withheld_reasons` per method.
- **Validation:** an unknown line, window, date or out-of-range number is **422** with the allowed
  values: `{"error": "validation_error", "detail": [{"loc": ["path", "line"], "message": "...",
  "allowed": ["Red", "Orange", ...]}]}`.
- **Other errors:** 404 `not_available` (the export lacks a table, with `tables_in_export`), 413
  (request or URL over 2 KiB), 502 `data_unavailable`, 504 `timeout`.
- **Caching:** `Cache-Control: public, max-age=300` on 200s, `no-store` on errors. CORS only for the
  configured origins. At most 500 rows per response (`provenance.truncated`).
- **Lines:** Red, Orange, Blue, Green-B, Green-C, Green-D, Green-E, Mattapan.
  **Windows:** `last_24h`, `last_7d` (default `last_24h`; a table with only 24 h returns no rows for 7 d).

## Endpoints (all `GET`, under `/v1`)

| Path | Returns |
| --- | --- |
| `/lines` | the eight lines with 24 h coverage and links to their endpoints |
| `/lines/{line}/reliability?window=` | predicted deviation and revision (24 h only); actual deviation and on-time share |
| `/lines/{line}/accuracy?window=` | accuracy of MBTA's predictions by horizon bucket |
| `/lines/{line}/headways?window=` | headways within the band, bunched, gaps |
| `/lines/{line}/countdown?window=` | countdown accuracy by station and countdown minute |
| `/lines/{line}/segments?window=&direction=` | headway health per segment (two neighbouring stops): `rows[]` of `direction_id, from_stop_id, from_stop_name, to_stop_id, to_stop_name, segment_order, health, pct_within_band, pct_bunched, pct_gap, n_headways, n_excluded_outage, disrupted, coverage_pct, withheld_reason`; `health` is `even` (within band 80 or more), `some_bunching` (60 or more) or `bunched_gaps`; the shares and `health` are null with a `withheld_reason` under 50 % coverage; `direction` 0 or 1, default both (R4 export) |
| `/lines/{line}/marey?direction=` | reconstructed arrivals of the last complete hour: `hour_start` and `rows[]` of `direction_id, trip_id, stop_id, stop_name, stop_sequence, arrival_ts, method, confidence, scheduled_ts` (UTC, `method` is `observed` or `estimated`; R4 export) |
| `/incidents?line=&from=&to=` | incidents (local dates, at most 31 days; default the 31 days to `metrics_as_of`) |
| `/forecast/{line}` | latest next-hour delay forecasts by direction and horizon, status, training gate |
| `/pipeline/health?days=` | daily pipeline runs, feed polls, duplicates, quarantine (1 to 30 days, default 7) |
| `/pipeline/heartbeat?hours=` | hourly capture heartbeat: `rows[]` of `hour_start, expected_runs, missed_runs, feed_gap_min, data_outage` (1 to 720 hours, default 24; R4 export) |
| `/slo` | service level objectives over 7 and 30 days |
| `/metrics` | metric definitions with `docs/METRICS.md` anchors |
| `/meta` | freshness, tables in the export, coverage rules, limits |

The three R4 endpoints answer `404 not_available` until the export holds their tables (`enable_r4`
on the transform, `docs/DEPLOY.md`). Their `data` is `{line, window?, direction, rows}` (`hours` for
the heartbeat, `hour_start` for Marey); timestamps are UTC (`...Z`). Marey and heartbeat rows carry
no coverage figure and are never withheld.

Definitions of every figure are in [METRICS.md](METRICS.md); the `metrics_md` field of each response
names the section.

## Examples

```sh
curl -s "$API/v1/lines/Red/reliability?window=last_24h"
```
```json
{"data": {"line": "Red", "window": "last_24h",
  "predicted_deviation": {"table": "agg_line_reliability_window", "metrics_md": "1-predicted-schedule-deviation",
    "rows": [{"line": "Red", "window": "last_24h", "median_predicted_deviation_s": 60.0,
              "p90_predicted_deviation_s": 100.0, "median_abs_revision_s": 5.0, "skipped_share": 0.1,
              "n_observations": 10, "coverage_pct": 90.0, "withheld_reason": null}]},
  "actual_deviation": {"...": "..."}},
 "coverage": {"...": "..."}, "freshness": {"...": "..."}, "provenance": {"...": "..."}}
```

A withheld value (Blue at 30% coverage): `"median_predicted_deviation_s": null, "coverage_pct": 30.0,
"withheld_reason": "coverage 30% is below 50%"`.

```sh
curl -s "$API/v1/incidents?line=Red&from=2026-11-20&to=2026-11-22"
curl -s "$API/v1/forecast/Red"
curl -s "$API/v1/pipeline/health?days=3"
curl -si "$API/v1/lines/Purple/reliability"   # 422, allowed: Red, Orange, Blue, ...
```

Forecast values are trip-weighted seconds of delay; a method whose inputs cover under 50% of the
trips is `null` with its reason in `withheld_reasons`. `status` is `baselines_only` until a line
passes the training gate (ADR-019); `model_forecast_s` is then null.

## Running it locally

`HEADWAY_DATA_URL=file://$PWD/.local/demo/public/boston uv run --group api uvicorn
headway_api.app:create_app --factory` serves a demo export (`make demo-data`). Tests:
`uv run pytest tests/unit/test_api*.py`; the image under Lambda conditions: `make api-smoke`.
