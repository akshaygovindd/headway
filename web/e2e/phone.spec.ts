import { expect, test } from "@playwright/test";

test.describe("375px phone", () => {
  test.use({ viewport: { width: 375, height: 800 } });

  test("the nav is one row of tabs that scrolls sideways inside itself", async ({ page }) => {
    await page.goto("/");
    const m = await page.getByRole("navigation", { name: "Main" }).locator("ul").evaluate((ul) => ({
      scroll: ul.scrollWidth,
      client: ul.clientWidth,
      overflowX: getComputedStyle(ul).overflowX,
      tops: [...ul.querySelectorAll("li")].map((li) => Math.round(li.getBoundingClientRect().top)),
    }));
    expect(m.overflowX).toBe("auto");
    expect(m.scroll).toBeGreaterThan(m.client);
    expect(new Set(m.tops).size).toBe(1);
  });

  test("line cards are departure-board rows, with chips on their own line", async ({ page }) => {
    await page.goto("/");
    const card = page.getByRole("list", { name: "Lines" }).locator("> li").first();
    const parts = await card.evaluate((li) => {
      const badge = li.querySelector('[class*="rounded"] , svg, span')!.getBoundingClientRect();
      const name = li.querySelector("h3")!.getBoundingClientRect();
      const metric = li.querySelector('[data-testid="metric"]')!.getBoundingClientRect();
      const chip = li.querySelector('[data-testid="disruption"]')?.getBoundingClientRect() ?? null;
      return { badgeTop: badge.top, nameTop: name.top, nameLeft: name.left, metricLeft: metric.left, metricBottom: metric.bottom, chipTop: chip?.top ?? null, nameBottom: name.bottom };
    });
    expect(parts.metricLeft).toBeGreaterThan(parts.nameLeft); // number to the right of the name
    if (parts.chipTop != null) expect(parts.chipTop).toBeGreaterThanOrEqual(parts.metricBottom - 1); // chips below the row
  });

  test("the key numbers stack in one column", async ({ page }) => {
    await page.goto("/lines/Blue");
    const lefts = await page.getByTestId("key-numbers").first().locator("> div").evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left)));
    expect(new Set(lefts).size).toBe(1);
  });

  test("the Marey chart shows the last 30 minutes with a link to the full chart", async ({ page }) => {
    await page.goto("/lines/Blue");
    const phone = page.getByTestId("marey-phone").first();
    await expect(phone).toBeVisible();
    await expect(page.getByTestId("marey-desktop").first()).toBeHidden();
    const link = phone.getByRole("link", { name: /Open full chart/ });
    await expect(link).toBeVisible();
    await link.click();
    await expect(page.getByTestId("marey-desktop").first()).toBeVisible();
  });

  test("the Green-C strip lists Cleveland Circle", async ({ page }) => {
    await page.goto("/lines/Green-C");
    await expect(page.getByTestId("station-strip-phone").first()).toContainText("Cleveland Circle");
  });
});

test.describe("1440px desktop", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  test("the Green-C strip labels both ends once, at the stop points", async ({ page }) => {
    await page.goto("/lines/Green-C");
    const labels = page.getByTestId("strip-labels").first().getByTestId("strip-label");
    await expect(labels.first()).not.toHaveText("Cleveland Circle"); // the northern end, whatever the feed calls it
    await expect(labels.last()).toHaveText("Cleveland Circle");
    await expect(page.getByTestId("strip-labels").first().getByText("Cleveland Circle", { exact: true })).toHaveCount(1);
  });
});
