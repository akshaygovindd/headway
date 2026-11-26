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
| `/lines/{line}/segments?window=&direction=` | headway health per segment (two neighbouring stops): `rows[]` of `direction_id, from_stop_id, from_stop_name, to_stop_id, to_stop_name, segment_order, health, pct_within_band, pct_bunched, pct_gap, n_headways, n_excluded_outage, disrupted, disruption_header, coverage_pct, withheld_reason`; `health` is `even` (within band 80 or more), `some_bunching` (60 or more) or `bunched_gaps`; the shares and `health` are null with a `withheld_reason` under 50 % coverage; `disruption_header` is the public MBTA alert header of the latest line-scope disruption of the last 24 hours that covers the segment (null when the segment is not `disrupted` or no text is exported); `direction` 0 or 1, default both (R4 export) |
| `/lines/{line}/trip-time?window=` | trip time against the timetable per direction and time band (see below; R4 export) |
| `/lines/{line}/trip-time-budget?from=&to=&window=` | travel time between two stations of the line per time band: median, 95th percentile, buffer time index (see below; R4 export) |
| `/lines/{line}/spacing-by-hour?date=` | headway adherence per service hour of one service day (see below) |
| `/lines/{line}/disruptions` | line-scope service disruptions of the last 24 hours with the public MBTA header text (see below; R4 export) |
| `/lines/{line}/marey?direction=` | reconstructed arrivals of the last complete hour: `hour_start` and `rows[]` of `direction_id, trip_id, stop_id, stop_name, stop_sequence, arrival_ts, method, confidence, scheduled_ts` (UTC, `method` is `observed` or `estimated`; R4 export) |
| `/incidents?line=&from=&to=` | incidents (local dates, at most 31 days; default the 31 days to `metrics_as_of`) |
| `/forecast/{line}` | latest next-hour delay forecasts by direction and horizon, status, training gate |
| `/pipeline/health?days=` | daily pipeline runs, feed polls, duplicates, quarantine (1 to 30 days, default 7) |
| `/pipeline/heartbeat?hours=` | hourly capture heartbeat: `rows[]` of `hour_start, expected_runs, missed_runs, feed_gap_min, data_outage` (1 to 720 hours, default 24; R4 export) |
| `/pipeline/checks?hours=` | data-quality checks over the last hours: fail and warn hours per check (see below; 1 to 720 hours, default 24) |
| `/slo` | service level objectives over 7 and 30 days |
| `/metrics` | metric definitions with `docs/METRICS.md` anchors |
| `/meta` | freshness, tables in the export, coverage rules, limits |

The R4 endpoints (segments, marey, heartbeat, trip-time, trip-time-budget, disruptions) answer `404 not_available` until the export holds their tables (`enable_r4`
on the transform, `docs/DEPLOY.md`). Their `data` is `{line, window?, direction, rows}` (`hours` for
the heartbeat, `hour_start` for Marey); timestamps are UTC (`...Z`). Marey and heartbeat rows carry
no coverage figure and are never withheld.

### Trip-time budget

`GET /lines/{line}/trip-time-budget?from=<station_id>&to=<station_id>&window=last_24h|last_7d`
(needs `agg_trip_time_budget`; R4 export). `from` and `to` are parent-station ids (`place-davis`),
different (with neither, `data.stations` lists the line's `{id, name}` and `rows` is empty), and both stops of a trip of the line in that order; an unknown station is a 422 whose
`allowed` lists the line's station ids, and a pair no trip serves (two branches) is a 422 too.

```json
{"line": "Red", "window": "last_24h", "unit": "seconds", "min_trips": 20,
 "from_station": {"id": "place-davis", "name": "Davis"},
 "to_station": {"id": "place-pktrm", "name": "Park Street"},
 "rows": [{"direction_id": 1, "time_band": "am_peak", "n_trips": 40, "n_scheduled_trips": 45,
           "n_excluded_outage": 0, "n_implausible": 0, "n_disruption": 0, "median_s": 840.0,
           "p95_s": 1260.0, "buffer_time_index": 0.5, "coverage_pct": 88.9, "status": "measured",
           "withheld_reason": null, "no_trips_reason": null}]}
```

One row per direction and time band, with `status` `measured`, `withheld` or `no_trips`.
`median_s`, `p95_s` and `buffer_time_index` ((p95 - median) / median) are null with a
`withheld_reason` when coverage is under 50 % **or** `n_trips` is under `min_trips` (20): a 95th
percentile of fewer trips is not a percentile. "Usually 14 min; leave 21 to arrive on time 95 % of
the time" is `median_s` and `p95_s` in minutes. Definition: METRICS.md, Trip-time budget.

### Trip time, spacing by hour, disruptions and checks

`GET /lines/{line}/trip-time?window=last_24h|last_7d` (needs `agg_ett_band_window`; R4 export):

```json
{"line": "Red", "window": "last_24h", "unit": "seconds",
 "rows": [{"direction_id": 0, "time_band": "am_peak", "n_trips": 8, "n_scheduled_trips": 10,
           "n_excluded_outage": 0, "n_implausible": 0, "n_disruption": 0,
           "median_ett_s": 90.0, "p90_ett_s": 150.0, "median_scheduled_s": 1800.0,
           "median_actual_s": 1890.0, "coverage_pct": 80.0, "status": "measured",
           "withheld_reason": null, "no_trips_reason": null}],
 "overall": {"n_trips": 10, "n_scheduled_trips": 20, "n_excluded_outage": 0, "n_implausible": 0,
             "n_disruption": 0, "median_ett_s": 80.0, "p90_ett_s": 140.0, "coverage_pct": 50.0,
             "status": "measured", "withheld_reason": null, "no_trips_reason": null}}
```

Rows are ordered by `direction_id`, then `am_peak, midday, pm_peak, evening, other`; a band with no
scheduled trip has no row (not in the export). `median_ett_s` is actual minus plan (positive is
slower); `median_scheduled_s` is the plan and `median_actual_s` plan plus ETT, both for the measured
trips. `status` tells three cases apart: `measured`; `no_trips` (`n_trips` is 0, so the UI may say
"No trips measured yet": `no_trips_reason` says whether trips were scheduled or all fell in
data-outage hours; the value fields are null and the row is not "withheld"); `withheld` (trips exist
but `coverage_pct` is under 50: the value fields are null and `withheld_reason` says why).
`overall` is the line over all directions and bands from `agg_ett_window` (null when that table or
the line's row is missing). This is an adapted approximation, not an official measure.

`GET /lines/{line}/spacing-by-hour?date=YYYY-MM-DD` (from `agg_headway_adherence_hourly`; `date` is a
service date, default the latest in the export):

```json
{"line": "Red", "service_date": "2026-11-22",
 "hours": [{"service_date": "2026-11-22", "hour_local": 5, "hour_start": "2026-11-22T10:00:00Z",
            "n_headways": 10, "pct_within_band": 80.0, "pct_bunched": 10.0, "pct_gap": 10.0,
            "scheduled_headways": 12, "coverage_pct": 83.3, "data_outage": false,
            "status": "measured", "withheld_reason": null}]}
```

Both directions are combined (shares weighted by valid headways, coverage as summed numerators over
denominators). Hours are in service-day order, 05:00 first and 01:00 (after midnight, same service
date) last; hours without any scheduled headway have no row and the current hour is partial.
`status` is `measured`, `withheld` (coverage under 50: shares null), `no_headways` (scheduled but no
valid headway: `n_headways` 0, shares null) or `outage` (`data_outage` true: Headway's capture was
down; the shares and coverage are null and `withheld_reason` says so). Outage hours are not counted in
the envelope coverage.

`GET /lines/{line}/disruptions` (needs `agg_service_disruptions`; R4 export) lists the line-scope
service-disruption alerts of the last 24 hours, active first, then latest first:

```json
{"line": "Red", "window": "last_24h",
 "rows": [{"alert_id": "a1", "effect": "SIGNIFICANT_DELAYS",
           "header_text": "Shuttle buses replace trains between Beta and Gamma",
           "first_seen": "2026-11-22T14:00:00Z", "last_seen": "2026-11-22T14:40:00Z",
           "is_active": true, "n_stations": 2, "from_station_name": "Beta",
           "to_station_name": "Gamma",
           "segments": [{"direction_id": 0, "from_stop_id": "b1", "to_stop_id": "c1"}]}]}
```

`header_text` is the public MBTA alert text, unchanged. `from_station_name` and `to_station_name` are
the first and last affected station along direction 0 (null when not on a segment); `segments` are
the segments that start or end at an affected station. `evidence_json` of incidents stays hidden.

`GET /pipeline/checks?hours=24` (from `agg_quality_checks_hourly`, in every export): over the last
`hours` hours ending at the latest hour, per check, the hours in `fail`, `warn` and `ok`; hours with
status `no_service` (and `no_data`, `no_baseline`) are not evaluated and not counted:

```json
{"hours": 24, "n_checks": 6, "n_failing_checks": 1, "n_warning_checks": 1,
 "checks": [{"check_name": "missed_runs", "n_hours_evaluated": 3, "n_fail_hours": 1,
             "n_warn_hours": 1, "n_ok_hours": 1, "worst_status": "fail"}]}
```

`worst_status` is `fail` if any evaluated hour failed, else `warn`, else `ok`, or `not_evaluated`.
`n_failing_checks` counts the checks whose worst status is `fail`, `n_warning_checks` those whose worst
status is `warn`: the trust strip's "N failing, M warnings" counts distinct checks, not check-hours.

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
