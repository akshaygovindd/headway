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
    if (what === "trip-time" || what === "spacing-by-hour" || what === "disruptions") {
      const body = load(`lines_${line}_${what}`);
      if (what === "trip-time") body.data.window = params.get("window") ?? "last_24h";
      if (what === "spacing-by-hour" && params.get("date")) body.data.service_date = params.get("date");
      return { status: 200, body };
    }
    if (what === "marey") return { status: 200, body: mareyFor(line, params) };
    return { status: 200, body: forLine(load(`lines_Red_${what}`), line, params.get("window")) };
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
