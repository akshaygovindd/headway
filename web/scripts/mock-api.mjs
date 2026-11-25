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

/** Fixtures are written for Red; other lines reuse them. Blue has too little data, to show it. */
function forLine(body, line) {
  const walk = (v) => {
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === "object") {
      const out = {};
      for (const [k, x] of Object.entries(v)) out[k] = k === "line" && x === "Red" ? line : walk(x);
      if (line === "Blue" && "coverage_pct" in out && "withheld_reason" in out) {
        for (const k of Object.keys(out)) {
          if (typeof out[k] === "number" && !["coverage_pct", "n_observations", "countdown_min"].includes(k)) out[k] = null;
        }
        out.coverage_pct = 30;
        out.withheld_reason = "coverage 30% is below 50%";
      }
      return out;
    }
    return v;
  };
  return walk(body);
}

export function respond(path) {
  if (path === "/v1/lines") {
    const b = load("lines");
    return { status: 200, body: b };
  }
  let m = path.match(/^\/v1\/lines\/([^/]+)\/(reliability|headways|accuracy|countdown)$/);
  if (m) {
    const [, line, what] = m;
    if (!LINES.includes(line)) return { status: 422, body: { error: "validation_error" } };
    return { status: 200, body: forLine(load(`lines_Red_${what}`), line) };
  }
  m = path.match(/^\/v1\/forecast\/([^/]+)$/);
  if (m) return { status: 200, body: forLine(load("forecast_Red"), m[1]) };
  const flat = { "/v1/incidents": "incidents", "/v1/slo": "slo", "/v1/metrics": "metrics", "/v1/meta": "meta", "/v1/pipeline/health": "pipeline_health" };
  if (flat[path]) return { status: 200, body: load(flat[path]) };
  return { status: 404, body: { error: "not_found" } };
}

export function startMockApi(port = 0) {
  const server = createServer((req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const { status, body } = respond(url.pathname);
    res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
    res.end(JSON.stringify(body));
  });
  return new Promise((resolve) => server.listen(port, "127.0.0.1", () => resolve(server)));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const server = await startMockApi(Number(process.env.PORT ?? 8000));
  console.log(`mock API on http://127.0.0.1:${server.address().port}`);
}
