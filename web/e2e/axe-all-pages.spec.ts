import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";
import { PAGES } from "./pages";

const axePath = createRequire(import.meta.url).resolve("axe-core/axe.min.js");

// Every page, at phone and desktop width: WCAG 2.0/2.1 A and AA plus axe's best-practice rules
// (page has one h1, landmarks, links distinguishable from text), and no horizontal page scroll.
for (const width of [375, 1440]) {
  test.describe(`axe on every page at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    for (const path of PAGES) {
      test(`${path}`, async ({ page }) => {
        await page.goto(path, { waitUntil: "networkidle" });
        await page.addScriptTag({ path: axePath });
        const violations = await page.evaluate(async () => {
          // @ts-expect-error axe is injected above
          const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] });
          return r.violations.map((x: { id: string; nodes: { target: unknown }[] }) => `${x.id}: ${x.nodes.map((n) => String(n.target)).join(" | ")}`);
        });
        expect(violations).toEqual([]);
        const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
        expect(m.doc).toBeLessThanOrEqual(m.win);
      });
    }
  });
}
