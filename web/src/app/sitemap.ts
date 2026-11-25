import type { MetadataRoute } from "next";
import { LINES } from "@/lib/map";
import { siteUrl } from "@/lib/site";

export const PATHS = ["/", "/incidents", "/forecast", "/pipeline", "/how-we-measure", "/data", "/about"];

export default function sitemap(): MetadataRoute.Sitemap {
  const base = siteUrl();
  return [...PATHS, ...LINES.map((l) => `/lines/${l}`)].map((p) => ({ url: `${base}${p}` }));
}
