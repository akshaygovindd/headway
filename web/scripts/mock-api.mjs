// A tiny stand-in for the Headway API that serves the fixtures in test/fixtures. Used for the
// build in CI (no network) and for working on the site without the Python API.
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

// Under vitest (jsdom) the global URL is not Node's, so file URLs fail; tests run from web/.
const dir =
  typeof window === "undefined"
    ? fileURLToPath(new URL("../test/fixtures/", import.meta.url))
    : `${process.cwd()}/test/fixtures/`;
const load = (name) => JSON.parse(readFileSync(`${dir}${name}.json`, "utf8"));
const LINES = ["Red", "Orange", "Blue", "Green-B", "Green-C", "Green-D", "Green-E", "Mattapan"];

/**
 * What each line looks like in the fixtures, mirroring real states: Red is withheld (35% cov.,
 * with a shuttle stretch), Orange is partial (68% cov.), Blue is shown at 87% evenly spaced on
 * 77% cov., the rest are plausible shown values.
 */
const STATE = {
  Red: { cov: 35 },
  Orange: { cov: 68 },
  Blue: { cov: 77, set: { pct_within_band: 87, pct_bunched: 5.4, pct_gap: 7.6, pct_on_time_window: 83.1 } },
  "Green-B": { cov: 75.4 },
  "Green-C": { cov: 71.7 },
  "Green-D": { cov: 78.3 },
  "Green-E": { cov: 86.7 },
  Mattapan: { cov: 82.4 },
};
const KEEP = ["coverage_pct", "n_observations", "countdown_min"];
const clamp = (x) => Math.min(99, Math.max(1, Math.round(x * 10) / 10));

/** Fixtures are written for Red; other lines reuse them with their own state and small, stable variation. */
function forLine(body, line, window) {
  const idx = LINES.indexOf(line);
  const st = STATE[line];
  const walk = (v) => {
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") {
      const out = {};
      for (const [k, x] of Object.entries(v)) {
        out[k] = k === "line" && x === "Red" ? line : k === "window" && window ? window : walk(x);
      }
      if ("coverage_pct" in out && "withheld_reason" in out && out.coverage_pct != null && st) {
        out.coverage_pct = st.cov;
        for (const k of Object.keys(out)) {
          const n = out[k];
          if (typeof n !== "number" || KEEP.includes(k)) continue;
          if (k.startsWith("pct_")) out[k] = clamp(n + ((idx * 37) % 11) - 5);
          else if (k.endsWith("_s") && k !== "countdown_min") out[k] = Math.round(n * (1 + (((idx * 37) % 11) - 5) / 20));
        }
        // Observation counts differ by line (Red keeps the fixture's own numbers).
        for (const k of Object.keys(out)) if (k.startsWith("n_") && typeof out[k] === "number" && k !== "n_excluded_outage") out[k] = Math.round(out[k] * (1 + idx * 0.17));
        if (typeof out.pct_within_band === "number") {
          out.pct_bunched = Math.round((100 - out.pct_within_band) * 4) / 10;
          out.pct_gap = Math.round((100 - out.pct_within_band - out.pct_bunched) * 10) / 10;
        }
        if (st.set) Object.assign(out, Object.fromEntries(Object.entries(st.set).filter(([k]) => k in out)));
        if (st.cov < 50) {
          for (const k of Object.keys(out)) if (typeof out[k] === "number" && !KEEP.includes(k)) out[k] = null;
          out.withheld_reason = `coverage ${Math.round(st.cov)}% is below 50%`;
        }
      }
      return out;
    }
    return v;
  };
  return walk(body);
}

/** Direction filter and window label for the R4 fixtures. */
function segmentsFor(line, params) {
  const body = load(`lines_${line}_segments`);
  const d = params.get("direction");
  if (d != null) body.data.rows = body.data.rows.filter((r) => String(r.direction_id) === d);
  body.data.window = params.get("window") ?? "last_24h";
  body.data.direction = d == null ? null : Number(d);
  // The API only joins alert text for the last 24 hours: on last_7d it can be null while disrupted.
  if (body.data.window === "last_7d") for (const r of body.data.rows) r.disruption_header = null;
  return body;
}

function mareyFor(line, params) {
  const body = load(`lines_${line}_marey`);
  const d = params.get("direction");
  if (d != null) body.data.rows = body.data.rows.filter((r) => String(r.direction_id) === d);
  body.data.direction = d == null ? null : Number(d);
  return body;
}

/** The budget fixtures hold a line's stations and a few pairs; the rest of the pairs are 422 like the API. */
function budgetFor(line, params) {
  const fx = load(`lines_${line}_trip-time-budget`);
  const base = load("lines_Red_trip-time");
  const window = params.get("window") ?? "last_24h";
  const envelope = (data, rows) => ({
    data: { line, window, unit: "seconds", min_trips: 20, from_station: null, to_station: null, stations: [], rows: [], ...data },
    coverage: { rows: rows.length, mean_coverage_pct: null, min_coverage_pct: null, withheld_rows: rows.filter((r) => r.withheld_reason).length, rule: base.coverage.rule },
    freshness: base.freshness,
    provenance: { ...base.provenance, tables: ["agg_trip_time_budget"], definition: "trip-time-budget-agg_trip_time_budget", metrics_md: "docs/METRICS.md#release-4-site-metrics" },
  });
  const from = params.get("from");
  const to = params.get("to");
  if (from == null && to == null) return { status: 200, body: envelope({ stations: fx.stations }, []) };
  const names = Object.fromEntries(fx.stations.map((s) => [s.id, s.name]));
  if (from == null || to == null || !(from in names) || !(to in names) || from === to) {
    return { status: 422, body: { error: "validation_error", detail: [{ loc: ["query"], message: "from and to must be two different stations of the line", allowed: Object.keys(names) }] } };
  }
  const rows = fx.pairs[`${from}|${to}`];
  if (!rows) return { status: 422, body: { error: "validation_error", detail: [{ loc: ["query"], message: `no scheduled ${line} trip runs from ${names[from]} to ${names[to]}` }] } };
  return { status: 200, body: envelope({ from_station: { id: from, name: names[from] }, to_station: { id: to, name: names[to] }, rows }, rows) };
}

export function respond(path, search = "") {
  const params = new URLSearchParams(search);
  if (path === "/v1/lines") {
    const b = load("lines");
    return { status: 200, body: b };
  }
  let m = path.match(/^\/v1\/lines\/([^/]+)\/(reliability|headways|accuracy|countdown|segments|marey|trip-time|spacing-by-hour|disruptions)$/);
  if (m) {
    const [, line, what] = m;
    if (!LINES.includes(line)) return { status: 422, body: { error: "validation_error" } };
    if (what === "segments") return { status: 200, body: segmentsFor(line, params) };
    if (what === "trip-time" || what === "spacing-by-hour" || what === "disruptions" || what === "countdown") {
      const body = load(`lines_${line}_${what}`);
      if (what === "countdown") body.data.window = params.get("window") ?? "last_24h";
      if (what === "trip-time") body.data.window = params.get("window") ?? "last_24h";
      if (what === "spacing-by-hour" && params.get("date")) body.data.service_date = params.get("date");
      return { status: 200, body };
    }
    if (what === "marey") return { status: 200, body: mareyFor(line, params) };
    return { status: 200, body: forLine(load(`lines_Red_${what}`), line, params.get("window")) };
  }
  if (path === "/v1/accessibility") {
    const win = params.get("window") ?? "last_24h";
    if (!["last_24h", "last_7d"].includes(win)) return { status: 422, body: { error: "validation_error" } };
    const body = load(`accessibility_${win}`);
    const line = params.get("line");
    if (line) {
      if (!LINES.includes(line)) return { status: 422, body: { error: "validation_error" } };
      body.data.line = line;
      body.data.stations = body.data.stations.filter((s) => s.lines.includes(line));
      body.data.n_stations_out_now = body.data.stations.filter((s) => s.currently_out).length;
    }
    return { status: 200, body };
  }
  if (path === "/v1/slow-zones") {
    // The captured-snapshot state; MOCK_SLOW_ZONES=not-captured serves the committed empty state.
    const body = load(process.env.MOCK_SLOW_ZONES === "not-captured" ? "slow-zones_not-captured" : "slow-zones");
    const line = params.get("line");
    body.data.window = params.get("window") ?? "last_7d";
    if (line) {
      if (!LINES.includes(line)) return { status: 422, body: { error: "validation_error" } };
      body.data.line = line;
      body.data.segments = body.data.segments.filter((s) => s.line === line);
      body.data.comparison = body.data.comparison.filter((c) => c.line === line);
      body.data.counts = Object.fromEntries(Object.keys(body.data.counts).map((k) => [k, body.data.comparison.filter((c) => c.status === k).length]));
    }
    return { status: 200, body };
  }
  m = path.match(/^\/v1\/lines\/([^/]+)\/trip-time-budget$/);
  if (m) {
    if (!LINES.includes(m[1])) return { status: 422, body: { error: "validation_error" } };
    return budgetFor(m[1], params);
  }
  m = path.match(/^\/v1\/forecast\/([^/]+)$/);
  if (m) return { status: 200, body: forLine(load("forecast_Red"), m[1]) };
  if (path === "/v1/pipeline/heartbeat") {
    const body = load("pipeline_heartbeat");
    const hours = Number(params.get("hours") ?? 24);
    body.data.rows = body.data.rows.slice(-hours);
    body.data.hours = hours;
    return { status: 200, body };
  }
  if (path === "/v1/pipeline/checks") {
    const body = load("pipeline_checks");
    body.data.hours = Number(params.get("hours") ?? 24);
    return { status: 200, body };
  }
  const flat = { "/v1/incidents": "incidents", "/v1/slo": "slo", "/v1/metrics": "metrics", "/v1/meta": "meta", "/v1/pipeline/health": "pipeline_health" };
  if (flat[path]) return { status: 200, body: load(flat[path]) };
  return { status: 404, body: { error: "not_found" } };
}

export function startMockApi(port = 0) {
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const { status, body } = respond(url.pathname, url.search);
    res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
    res.end(JSON.stringify(body));
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const server = await startMockApi(Number(process.env.PORT ?? 8000));
  console.log(`mock API on http://127.0.0.1:${server.address().port}`);
}
