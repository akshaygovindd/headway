import type { ReactElement } from "react";
import { vi } from "vitest";
import { respond } from "../scripts/mock-api.mjs";

/** Serve the same fixtures the CI build uses, without a network. */
export function stubFixtureApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      const { status, body } = respond(url.pathname);
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    }),
  );
}

export const PAGES: { name: string; load: () => Promise<ReactElement> }[] = [
  { name: "/", load: async () => (await import("@/app/page")).default() },
  { name: "/lines/Red", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Red" }) }) },
  { name: "/lines/Blue (withheld)", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Blue" }) }) },
  { name: "/lines/Mattapan", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Mattapan" }) }) },
  { name: "/incidents", load: async () => (await import("@/app/incidents/page")).default() },
  { name: "/forecast", load: async () => (await import("@/app/forecast/page")).default() },
  { name: "/pipeline", load: async () => (await import("@/app/pipeline/page")).default() },
  { name: "/how-we-measure", load: async () => (await import("@/app/how-we-measure/page")).default() },
  { name: "/about", load: async () => (await import("@/app/about/page")).default() },
  { name: "/data", load: async () => (await import("@/app/data/page")).default() },
];
