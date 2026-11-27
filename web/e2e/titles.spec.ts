import { expect, test } from "@playwright/test";
import { PAGES } from "./pages";

const HOME = "Headway — Boston subway reliability you can check";

test("the home page is titled with the site name and tagline in the tab and the share cards", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(HOME);
  await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", HOME);
  await expect(page.locator('meta[name="twitter:title"]')).toHaveAttribute("content", HOME);
  await expect(page.locator('meta[property="og:site_name"]')).toHaveAttribute("content", "Headway");
});

test("every other page has its own title, ending in the site name, repeated in og:title", async ({ page }) => {
  const seen = new Map<string, string>();
  for (const path of PAGES.filter((p) => p !== "/" && !p.includes("?"))) {
    await page.goto(path);
    const title = await page.title();
    expect(title, path).toMatch(/\S.* \| Headway$/);
    expect(title, path).not.toBe(HOME);
    await expect(page.locator('meta[property="og:title"]'), path).toHaveAttribute("content", title);
    expect(seen.get(title), `${path} repeats the title of ${seen.get(title)}`).toBeUndefined();
    seen.set(title, path);
  }
});
