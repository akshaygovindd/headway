import { expect, test } from "@playwright/test";
import { PAGES } from "./pages";

for (const width of [375, 1440]) {
  test.describe(`${width}px wide`, () => {
    test.use({ viewport: { width, height: 900 } });
    for (const path of PAGES) {
      test(`${path} has no horizontal page scroll`, async ({ page }) => {
        await page.goto(path, { waitUntil: "networkidle" });
        const m = await page.evaluate(() => ({
          doc: document.documentElement.scrollWidth,
          body: document.body.scrollWidth,
          win: window.innerWidth,
        }));
        expect(m.doc, `document ${m.doc} > viewport ${m.win}`).toBeLessThanOrEqual(m.win);
        expect(m.body, `body ${m.body} > viewport ${m.win}`).toBeLessThanOrEqual(m.win);
      });
    }
  });
}
