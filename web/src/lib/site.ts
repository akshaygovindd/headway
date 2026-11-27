import type { Metadata } from "next";

export const SITE_NAME = "Headway";

/** Public origin for canonical URLs, the sitemap and share cards. */
export function siteUrl(): string {
  return (process.env.NEXT_PUBLIC_SITE_URL ?? "http://localhost:3000").replace(/\/+$/, "");
}

export const HOME_TITLE = "Headway \u2014 Boston subway reliability you can check";

/**
 * Per-page metadata: title, description, canonical path and the matching share cards. The home page
 * uses the full site title as it is; every other page is "<title> | Headway" in the tab and the cards.
 */
export function pageMeta(title: string, description: string, path: string): Metadata {
  const home = path === "/";
  const full = home ? HOME_TITLE : `${title} | ${SITE_NAME}`;
  return {
    title: home ? { absolute: HOME_TITLE } : title,
    description,
    alternates: { canonical: path },
    openGraph: { title: full, description, url: path, siteName: SITE_NAME, type: "website", images: ["/opengraph-image"] },
    twitter: { card: "summary_large_image", title: full, description, images: ["/twitter-image"] },
  };
}
