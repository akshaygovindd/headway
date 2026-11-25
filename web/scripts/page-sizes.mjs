// Prints per-page HTML and first-load JavaScript sizes from a finished `next build`.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { gzipSync } from "node:zlib";
import path from "node:path";

const out = path.resolve(".next");
const pages = path.join(out, "server", "app");
const walk = (d) => readdirSync(d).flatMap((f) => (statSync(path.join(d, f)).isDirectory() ? walk(path.join(d, f)) : [path.join(d, f)]));
const kb = (n) => `${(n / 1024).toFixed(1)} kB`;

const rows = walk(pages)
  .filter((f) => f.endsWith(".html") && !/_not-found|_global-error/.test(f))
  .sort()
  .map((f) => {
    const html = readFileSync(f);
    const scripts = [...new Set([...html.toString().matchAll(/<script[^>]+src="(\/_next\/[^"]+\.js)"/g)].map((m) => m[1]))];
    const js = scripts.reduce((n, s) => n + gzipSync(readFileSync(path.join(out, s.replace("/_next/", "")))).length, 0);
    const name = "/" + path.relative(pages, f).replace(/\.html$/, "").replace(/^index$/, "");
    return { name, html: gzipSync(html).length, js, n: scripts.length };
  });
console.log("| Route | HTML (gzip) | First-load JS (gzip) | Scripts |\n| --- | --- | --- | --- |");
for (const r of rows) console.log(`| \`${r.name}\` | ${kb(r.html)} | ${kb(r.js)} | ${r.n} |`);
