import { afterEach, describe, expect, it, vi } from "vitest";
import robots from "@/app/robots";
import sitemap from "@/app/sitemap";
import { HOME_TITLE, pageMeta, siteUrl } from "@/lib/site";

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
    expect(m.openGraph).toMatchObject({ title: "Incidents | Headway", description: "desc", url: "/incidents" });
    expect(m.twitter).toMatchObject({ card: "summary_large_image", images: ["/twitter-image"] });
    expect(m.openGraph).toMatchObject({ images: ["/opengraph-image"] });
    expect(m.title).toBe("Incidents");
    expect(m.twitter).toMatchObject({ title: "Incidents | Headway" });
  });

  it("titles the home page with the site name and tagline, and no template suffix", () => {
    const m = pageMeta("ignored", "desc", "/");
    expect(HOME_TITLE).toBe("Headway \u2014 Boston subway reliability you can check");
    expect(m.title).toEqual({ absolute: HOME_TITLE });
    expect(m.openGraph).toMatchObject({ title: HOME_TITLE });
    expect(m.twitter).toMatchObject({ title: HOME_TITLE });
  });
});
