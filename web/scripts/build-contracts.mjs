// Builds src/data/contracts.json from the repo's existing sources, so the /data page needs no
// Python at build or run time. Sources:
//   dbt/headway/models/marts/_aggregates.yml  descriptions, grain, columns, dbt tests
//   src/headway_api/queries.py                which gold tables the API serves, value columns, anchors
//   docs/METRICS.md                           headings, to prove every anchor exists
//   src/data/gold-column-types.json           the real Parquet type of every gold column (the
//                                             gold export manifest's column_types, snapshot by
//                                             scripts/snapshot_gold_types.py)
// Output is deterministic and committed. `node scripts/build-contracts.mjs --check` fails if it drifted.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { load as yamlLoad } from "js-yaml";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const read = (p) => fs.readFileSync(path.join(root, p), "utf8");
const OUT = path.join(root, "web", "src", "data", "contracts.json");

export const slug = (heading) =>
  heading
    .trim()
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s-]/gu, "")
    .replace(/\s/g, "-");

// Endpoints the API serves per table (src/headway_api/app.py routes).
const ENDPOINTS = {
  agg_line_reliability_window: ["/v1/lines/{line}/reliability"],
  agg_actual_deviation_window: ["/v1/lines/{line}/reliability"],
  agg_accuracy_window: ["/v1/lines/{line}/accuracy"],
  agg_headway_window: ["/v1/lines/{line}/headways"],
  agg_headway_segment_window: ["/v1/lines/{line}/segments"],
  agg_marey_last_hour: ["/v1/lines/{line}/marey"],
  agg_capture_heartbeat_hourly: ["/v1/pipeline/heartbeat"],
  agg_ett_band_window: ["/v1/lines/{line}/trip-time"],
  agg_ett_window: ["/v1/lines/{line}/trip-time"],
  agg_trip_time_budget: ["/v1/lines/{line}/trip-time-budget"],
  agg_slow_segments: ["/v1/slow-zones"],
  agg_slow_zone_comparison: ["/v1/slow-zones"],
  agg_headway_adherence_hourly: ["/v1/lines/{line}/spacing-by-hour"],
  agg_service_disruptions: ["/v1/lines/{line}/disruptions", "/v1/lines/{line}/segments"],
  agg_quality_checks_hourly: ["/v1/pipeline/checks"],
  agg_countdown_accuracy_window: ["/v1/lines/{line}/countdown"],
  agg_incidents: ["/v1/incidents"],
  agg_forecast_next_hour: ["/v1/forecast/{line}"],
  agg_forecast_gate_status: ["/v1/forecast/{line}"],
  agg_slo_rollup: ["/v1/slo"],
  agg_pipeline_health: ["/v1/pipeline/health"],
};
// Tables outside queries.py SECTIONS and their METRICS.md (or other doc) targets.
const EXTRA_DOCS = {
  agg_incidents: "incidents-release-3",
  agg_forecast_next_hour: "forecast-serving-and-comparison-release-3",
  agg_forecast_gate_status: "forecast-serving-and-comparison-release-3",
  agg_slo_rollup: "docs/SLO.md",
  agg_pipeline_health: "docs/SLO.md",
  agg_headway_segment_window: "release-4-site-metrics",
  agg_marey_last_hour: "release-4-site-metrics",
  agg_capture_heartbeat_hourly: "release-4-site-metrics",
  agg_ett_band_window: "release-4-site-metrics",
  agg_ett_window: "release-4-site-metrics",
  agg_trip_time_budget: "release-4-site-metrics",
  agg_slow_segments: "release-4-site-metrics",
  agg_slow_zone_comparison: "release-4-site-metrics",
  agg_headway_adherence_hourly: "release-4-site-metrics",
  agg_service_disruptions: "release-4-site-metrics",
  agg_quality_checks_hourly: "release-4-site-metrics",
};

const TYPE_RULES = [
  [/(_ts|_at)$/, "timestamp"],
  [/(^|_)date$/, "date"],
  [/^(is_|has_)|^(met|gate_passed|counts_toward_slo)$/, "boolean"],
  [/^(n|n_.*|.*_count|.*_numerator|.*_denominator|direction_id|hour_local|countdown_min|.*_days|min_days|min_weekend_days|good_events|total_events|prediction_horizon_min|horizon_min)$/, "integer"],
  [/^(scheduled_trip_stops|scheduled_headways|days_covered|vehicle_polls_.*|.*_bytes|quarantine_.*)$/, "integer"],
  [/^(persistence|daytype_hour|rolling_median_60m|mbta_reference)$/, "double"],
  [/^(source|model_version|number_check|llm_model|thresholds_crossed|matched_alert_ids)$/, "string"],
  [/(^|_)(id|name|text|reason|status|label|bucket|hash|json|line|window|method|slo_name|metric|template_used|run_id)$|^line$/, "string"],
  [/(_s|_pct|_ratio|_share|_rate)$|^(pct_|median_|p90_|mae|target_pct|actual_pct|error_budget_used)/, "double"],
];
// DuckDB / Parquet type names the gold export writes, mapped to the contract vocabulary.
const PARQUET_TYPES = {
  VARCHAR: "string",
  BIGINT: "integer",
  INTEGER: "integer",
  SMALLINT: "integer",
  TINYINT: "integer",
  UBIGINT: "integer",
  DOUBLE: "double",
  FLOAT: "double",
  BOOLEAN: "boolean",
  DATE: "date",
  TIMESTAMP: "timestamp",
  "TIMESTAMP WITH TIME ZONE": "timestamp",
};
export function parquetType(t) {
  if (t.endsWith("[]")) return `${parquetType(t.slice(0, -2))}[]`;
  if (/^DECIMAL\(/.test(t)) return "double";
  const mapped = PARQUET_TYPES[t];
  if (!mapped) throw new Error(`gold-column-types.json: unmapped Parquet type ${t}`);
  return mapped;
}

export function inferType(name) {
  for (const [re, t] of TYPE_RULES) if (re.test(name)) return t;
  return "unclassified";
}

function testName(t) {
  if (typeof t === "string") return { name: t };
  const [name, body] = Object.entries(t)[0];
  const args = body?.arguments ?? {};
  const severity = body?.config?.severity;
  return { name, args: Object.keys(args).length ? args : undefined, severity };
}
const describeCheck = (t) => {
  const bits = [];
  if (t.args) bits.push(Object.entries(t.args).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", "));
  if (t.severity) bits.push(`severity ${t.severity}`);
  return bits.join("; ") || null;
};

export function parseSections(py) {
  const out = {};
  const re = /Section\(\s*"([^"]+)",\s*"([^"]+)",\s*\(([^)]*)\),\s*\(([^)]*)\),\s*"([^"]+)",\s*"([^"]+)",\s*"((?:[^"\\]|\\.)*)",?\s*\)/g;
  const strs = (s) => [...s.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  for (const m of py.matchAll(re)) {
    out[m[2]] = { section: m[1], keys: strs(m[3]), values: strs(m[4]), n_column: m[5], anchor: m[6], label: m[7] };
  }
  return out;
}

export function build() {
  const sections = parseSections(read("src/headway_api/queries.py"));
  const apiTables = Object.keys(ENDPOINTS);
  const sectionTables = Object.keys(sections);
  const missing = sectionTables.filter((t) => !ENDPOINTS[t]);
  if (sectionTables.length !== 5 || missing.length) {
    throw new Error(`queries.py SECTIONS changed shape (parsed ${sectionTables.length}; unmapped: ${missing})`);
  }

  const anchors = new Set(
    [...read("docs/METRICS.md").matchAll(/^#{1,6}\s+(.+)$/gm)].map((m) => slug(m[1])),
  );
  const docUrl = (target) => {
    if (target.startsWith("docs/")) {
      if (!fs.existsSync(path.join(root, target))) throw new Error(`missing doc ${target}`);
      return target;
    }
    if (!anchors.has(target)) throw new Error(`METRICS.md has no heading for anchor ${target}`);
    return `docs/METRICS.md#${target}`;
  };

  const realTypes = JSON.parse(read("web/src/data/gold-column-types.json"));
  const models = new Map();
  for (const f of ["dbt/headway/models/marts/_aggregates.yml"]) {
    for (const m of yamlLoad(read(f)).models) models.set(m.name, m);
  }

  const tables = apiTables.sort().map((name) => {
    const m = models.get(name);
    if (!m) throw new Error(`no dbt model for ${name}`);
    const sec = sections[name];
    const columns = (m.columns ?? []).map((c) => {
      const tests = (c.data_tests ?? c.tests ?? []).map(testName);
      const real = realTypes[name]?.[c.name];
      return {
        name: c.name,
        type: real ? parquetType(real) : inferType(c.name),
        ...(real ? {} : { type_derived: true }),
        nullable: !tests.some((t) => t.name === "not_null"),
        description: c.description ?? "",
      };
    });
    const hasCoverage = columns.some((c) => c.name === "coverage_pct");
    const checks = [];
    for (const t of (m.data_tests ?? m.tests ?? []).map(testName)) {
      checks.push({ scope: "table", name: t.name, detail: describeCheck(t) });
    }
    for (const c of m.columns ?? []) {
      for (const t of (c.data_tests ?? c.tests ?? []).map(testName)) {
        checks.push({ scope: c.name, name: t.name, detail: describeCheck(t) });
      }
    }
    return {
      name,
      description: m.description ?? "",
      grain: m.config?.meta?.grain ?? "",
      endpoints: ENDPOINTS[name],
      value_columns: sec?.values ?? [],
      n_column: sec?.n_column ?? null,
      definition: docUrl(sec?.anchor ?? EXTRA_DOCS[name]),
      freshness:
        "Refreshed with each gold export (about every 15 minutes). The API caches responses for 300 s; the site marks data stale once the export is older than 60 minutes.",
      coverage: hasCoverage
        ? "A value from a row with coverage_pct under 50 is withheld (null) with withheld_reason. A zero denominator gives no coverage figure and is withheld too."
        : "No coverage column. Values are served as stored.",
      columns,
      checks,
    };
  });

  return {
    sources: [
      "dbt/headway/models/marts/_aggregates.yml",
      "src/headway_api/queries.py",
      "docs/METRICS.md",
    ],
    type_note:
      "Types are the real Parquet column types of the gold export (its manifest's column_types). A column the export has not been snapshotted for is typed by naming convention and marked type_derived. Nullability is derived from not_null tests.",
    tables,
  };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const text = JSON.stringify(build(), null, 2) + "\n";
  if (process.argv.includes("--check")) {
    const cur = fs.existsSync(OUT) ? fs.readFileSync(OUT, "utf8") : "";
    if (cur !== text) {
      console.error("contracts.json is out of date: run `npm run contracts`");
      process.exit(1);
    }
    console.log("contracts.json is up to date");
  } else {
    fs.writeFileSync(OUT, text);
    console.log(`wrote ${path.relative(root, OUT)}`);
  }
}
