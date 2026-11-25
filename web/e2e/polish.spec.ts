import { expect, test } from "@playwright/test";

for (const width of [375, 1440]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height: 900 } });

    test("tap targets are at least 44px tall: nav, line names, footer, CSV, pipeline link", async ({ page }) => {
      await page.goto("/");
      const targets = [
        page.getByRole("navigation", { name: "Main" }).getByRole("link"),
        page.getByRole("list", { name: "Lines" }).getByRole("link"),
        page.getByRole("contentinfo").getByRole("link"),
        page.getByRole("link", { name: "Pipeline health and checks" }),
      ];
      for (const t of targets) {
        for (const el of await t.all()) {
          const box = await el.boundingBox();
          expect(box?.height ?? 0, `${await el.textContent()}`).toBeGreaterThanOrEqual(43.5);
        }
      }
      await page.goto("/lines/Blue");
      const csv = await page.getByRole("button", { name: "Download CSV" }).boundingBox();
      expect(csv?.height ?? 0).toBeGreaterThanOrEqual(43.5);
    });

    test("nav links are ink, underlined on hover, and the current page has a 2px underline", async ({ page }) => {
      await page.goto("/incidents");
      const nav = page.getByRole("navigation", { name: "Main" });
      const current = nav.getByRole("link", { name: "Incidents" });
      const other = nav.getByRole("link", { name: "Forecast" });
      const style = (l: typeof current) =>
        l.evaluate((a) => {
          const s = getComputedStyle(a);
          return { color: s.color, line: s.textDecorationLine, thick: s.textDecorationThickness };
        });
      expect((await style(current)).color).toBe("rgb(16, 20, 26)");
      expect(await style(current)).toMatchObject({ line: "underline", thick: "2px" });
      expect(await style(other)).toMatchObject({ color: "rgb(16, 20, 26)", line: "none" });
      await other.hover();
      expect((await style(other)).line).toBe("underline");
      await other.focus();
      await page.keyboard.press("Shift+Tab");
      await page.keyboard.press("Tab");
      expect((await style(other)).line).toBe("underline");
    });
  });
}

test("line names on the home page are ink", async ({ page }) => {
  await page.goto("/");
  const color = await page.getByRole("list", { name: "Lines" }).getByRole("link").first().evaluate((a) => getComputedStyle(a).color);
  expect(color).toBe("rgb(16, 20, 26)");
});

test("the hero is regular weight with bold figures, at most about 60 characters a line", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  const hero = page.getByTestId("hero");
  expect(await hero.evaluate((h) => getComputedStyle(h).fontWeight)).toBe("400");
  const strong = await hero.locator("strong").evaluateAll((els) => els.map((e) => ({ text: e.textContent, w: getComputedStyle(e).fontWeight })));
  expect(strong.length).toBeGreaterThan(0);
  for (const s of strong) {
    expect(s.text).toMatch(/\d/);
    expect(Number(s.w)).toBeGreaterThanOrEqual(700);
  }
  const longest = await hero.evaluate((h) => {
    const counts = new Map<number, number>();
    const walker = document.createTreeWalker(h, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const t = n as Text;
      for (let i = 0; i < t.length; i++) {
        const r = document.createRange();
        r.setStart(t, i);
        r.setEnd(t, i + 1);
        const rect = r.getClientRects()[0];
        if (rect) counts.set(Math.round(rect.top), (counts.get(Math.round(rect.top)) ?? 0) + 1);
      }
    }
    return Math.max(...counts.values());
  });
  expect(longest).toBeLessThanOrEqual(66);
});

test("the how-to-read key is a list with a fixed icon column", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/", { waitUntil: "networkidle" });
  const widths = await page.getByTestId("state-icon").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().width)));
  expect(widths).toHaveLength(5);
  expect(new Set(widths).size).toBe(1);
  const columns = () => page.getByTestId("state-key").locator("> li").evaluateAll((els) => new Set(els.map((e) => Math.round(e.getBoundingClientRect().left))).size);
  await expect.poll(columns).toBe(2); // two columns
});
