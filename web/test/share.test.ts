import { afterEach, describe, expect, it, vi } from "vitest";
import robots from "@/app/robots";
import sitemap from "@/app/sitemap";
import { pageMeta, siteUrl } from "@/lib/site";

afterEach(() => vi.unstubAllEnvs());

describe("share metadata", () => {
  it("builds canonical URLs from NEXT_PUBLIC_SITE_URL, without a trailing slash", () => {
    vi.stubEnv("NEXT_PUBLIC_SITE_URL", "https://headway.example/");
    expect(siteUrl()).toBe("https://headway.example");
    const urls = sitemap().map((e) => e.url);
    expect(urls).toContain("https://headway.example/");
    expect(urls).toContain("https://headway.example/data");
    expect(urls).toContain("https://headway.example/lines/Green-B");
    expect(urls.every((u) => u.startsWith("https://headway.example/"))).toBe(true);
    expect(robots().sitemap).toBe("https://headway.example/sitemap.xml");
  });

  it("gives each page a title, description, canonical path and cards", () => {
    const m = pageMeta("Incidents", "desc", "/incidents");
    expect(m.alternates?.canonical).toBe("/incidents");
    expect(m.openGraph).toMatchObject({ title: "Incidents", description: "desc", url: "/incidents" });
    expect(m.twitter).toMatchObject({ card: "summary_large_image", images: ["/twitter-image"] });
    expect(m.openGraph).toMatchObject({ images: ["/opengraph-image"] });
  });
});
