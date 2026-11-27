// A tiny stand-in for the Headway API that serves recorded answers of the real API. Used for the
// build in CI (no network) and for working on the site without the Python API.
//
//   test/fixtures/real       what the real API answers over the committed real gold snapshot, one file
//                            per request (scripts/build_web_fixtures.py writes them; CI fails when stale)
//   test/fixtures/synthetic  a few hand-made edge states the snapshot lacks (README there); a synthetic
//                            file is served in place of the real one for the same request
//
// A request with no file is a 404 `no_fixture`, never a made-up answer.
import { existsSync, readFileSync } from "node:fs";
import { createServer } from "node:http";
import { fileURLToPath } from "node:url";

// Under vitest (jsdom) the global URL is not Node's, so file URLs fail; tests run from web/.
const dir =
  typeof window === "undefined"
    ? fileURLToPath(new URL("../test/fixtures/", import.meta.url))
    : `${process.cwd()}/test/fixtures/`;

/** File name of a request: the path under /v1, "/" as "__", then the sorted query as --key=value. */
export function slug(path, search = "") {
  const params = new URLSearchParams(search);
  let name = path.replace(/^\/v1\//, "").replaceAll("/", "__");
  for (const k of [...params.keys()].sort()) name += `--${k}=${params.get(k)}`;
  return name;
}

export function respond(path, search = "") {
  const name = `${slug(path, search)}.json`;
  // MOCK_SLOW_ZONES=not-captured: the real snapshot's state (no official list captured yet, nothing flagged).
  const skipSynthetic = process.env.MOCK_SLOW_ZONES === "not-captured" && name.startsWith("slow-zones");
  for (const sub of skipSynthetic ? ["real"] : ["synthetic", "real"]) {
    if (existsSync(`${dir}${sub}/${name}`)) {
      const doc = JSON.parse(readFileSync(`${dir}${sub}/${name}`, "utf8"));
      return { status: doc.status, body: doc.body };
    }
  }
  return { status: 404, body: { error: "no_fixture", request: `GET ${path}${search}`, hint: "add the request to scripts/build_web_fixtures.py and run npm run fixtures" } };
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
