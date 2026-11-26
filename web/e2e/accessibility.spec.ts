import { createRequire } from "node:module";
import { expect, test } from "@playwright/test";

const axePath = createRequire(import.meta.url).resolve("axe-core/axe.min.js");

for (const width of [375, 1440]) {
  test.describe(`accessibility page at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("no horizontal page scroll, no axe violations, out-now stations marked in words", async ({ page }) => {
      await page.goto("/accessibility", { waitUntil: "networkidle" });
      const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
      expect(m.doc).toBeLessThanOrEqual(m.win);
      await expect(page.getByTestId("out-now-summary")).toContainText("3 of 125 stations");
      await expect(page.getByTestId("out-now").first()).toContainText("Out now");
      await page.addScriptTag({ path: axePath });
      const v = await page.evaluate(async () => {
        // @ts-expect-error axe is injected above
        const r = await window.axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21aa"] });
        return r.violations.map((x: { id: string; nodes: { target: unknown }[] }) => `${x.id}: ${x.nodes.map((n) => String(n.target)).join(" | ")}`);
      });
      expect(v).toEqual([]);
    });

    test("the main navigation still fits and has no overflow", async ({ page }) => {
      await page.goto("/accessibility");
      const nav = page.getByRole("navigation", { name: "Main" });
      await expect(nav.getByRole("link", { name: "Elevators" })).toHaveAttribute("aria-current", "page");
      const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
      expect(m.doc).toBeLessThanOrEqual(m.win);
    });
  });
}
