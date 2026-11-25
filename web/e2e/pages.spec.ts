import { expect, test } from "@playwright/test";
import { PAGES } from "./pages";

for (const path of PAGES) {
  test(`${path} renders content with no failed requests or console errors`, async ({ page }) => {
    const bad: string[] = [];
    page.on("response", (r) => {
      if (r.status() >= 400) bad.push(`${r.status()} ${r.url()}`);
    });
    page.on("console", (m) => {
      if (m.type() === "error") bad.push(`console: ${m.text()}`);
    });
    await page.goto(path, { waitUntil: "networkidle" });
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    // The loading skeleton must be gone once the page has streamed in.
    await expect(page.locator('[aria-busy="true"]')).toHaveCount(0);
    expect(bad).toEqual([]);
  });
}

test("an unknown line is a real 404 page", async ({ page }) => {
  const res = await page.goto("/lines/Nope");
  expect(res?.status()).toBe(404);
  await expect(page.getByRole("heading", { level: 1 })).toContainText(/not found|page/i);
});
