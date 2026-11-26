import { createRequire } from "node:module";
import { expect, test, type Page } from "@playwright/test";

const axePath = createRequire(import.meta.url).resolve("axe-core/axe.min.js");

async function violations(page: Page) {
  await page.addScriptTag({ path: axePath });
  return page.evaluate(async () => {
    // @ts-expect-error axe is injected above
    const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa"] });
    return r.violations.map((v: { id: string; nodes: { target: unknown }[] }) => `${v.id}: ${v.nodes.map((n) => String(n.target)).join(" | ")}`);
  });
}

for (const width of [375, 1440]) {
  test.describe(`slow zones at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("no horizontal page scroll, no axe violations, provenance and the three-way table visible", async ({ page }) => {
      await page.goto("/slow-zones", { waitUntil: "networkidle" });
      const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
      expect(m.doc).toBeLessThanOrEqual(m.win);
      await expect(page.getByTestId("official-provenance")).toContainText("captured 2026-11-20");
      await expect(page.getByTestId("official-provenance")).toContainText("not a live feed");
      await expect(page.getByRole("table", { name: /compared with the MBTA's list/ })).toBeAttached();
      expect(await page.getByTestId("slow-line").count()).toBeGreaterThan(0);
      expect(await violations(page)).toEqual([]);
    });

    test("the tables scroll inside their box, the page does not", async ({ page }) => {
      await page.goto("/slow-zones", { waitUntil: "networkidle" });
      const wide = await page.evaluate(() => [...document.querySelectorAll("table")].map((t) => t.parentElement!.scrollWidth <= t.parentElement!.clientWidth || getComputedStyle(t.parentElement!).overflowX === "auto"));
      expect(wide.every(Boolean)).toBe(true);
    });
  });
}
