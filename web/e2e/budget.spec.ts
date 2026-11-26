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

const STATES = [
  ["measured pair", "/lines/Red/budget?from=place-davis&to=place-harvard"],
  ["every band withheld", "/lines/Red/budget?from=place-alewife&to=place-braintree&window=last_24h"],
  ["no trip between them", "/lines/Red/budget?from=place-braintree&to=place-davis"],
  ["no pair chosen", "/lines/Red/budget"],
] as const;

for (const width of [375, 1440]) {
  test.describe(`trip-time budget at ${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    for (const [name, path] of STATES) {
      test(`${name}: no horizontal scroll and no axe violations`, async ({ page }) => {
        await page.goto(path, { waitUntil: "networkidle" });
        const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, win: window.innerWidth }));
        expect(m.doc).toBeLessThanOrEqual(m.win);
        expect(await violations(page)).toEqual([]);
      });
    }

    test("the line page form takes two stations to the answer, with tap targets of 44 px", async ({ page }) => {
      await page.goto("/lines/Red", { waitUntil: "networkidle" });
      const form = page.getByTestId("budget-form");
      for (const el of await form.locator("select, button").all()) {
        expect((await el.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(43.5);
      }
      await form.locator("select[name=from]").selectOption("place-davis");
      await form.locator("select[name=to]").selectOption("place-harvard");
      await form.getByRole("button", { name: "Show the budget" }).click();
      await expect(page).toHaveURL(/\/lines\/Red\/budget\?from=place-davis&to=place-harvard/);
      await expect(page.getByTestId("budget-sentence")).toContainText("Usually");
      await expect(page.getByTestId("budget-sentence")).toContainText("to arrive on time 95% of the time");
      // the popover opens from a number
      await page.getByTestId("budget-sentence").getByRole("button").first().click();
      await expect(page.getByRole("dialog")).toBeVisible();
    });
  });
}
