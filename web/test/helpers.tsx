import { Children, type ReactElement, type ReactNode } from "react";
import RootLayout from "@/app/layout";
import { vi } from "vitest";
import { respond } from "../scripts/mock-api.mjs";

/** Serve the same fixtures the CI build uses, without a network. */
export function stubFixtureApi() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      const { status, body } = respond(url.pathname, url.search);
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    }),
  );
}

export const PAGES: { name: string; load: () => Promise<ReactElement> }[] = [
  { name: "/", load: async () => (await import("@/app/page")).default() },
  { name: "/lines/Red (withheld, shuttle)", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Red" }) }) },
  { name: "/lines/Orange (partial)", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Orange" }) }) },
  { name: "/lines/Blue", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Blue" }) }) },
  { name: "/lines/Green-D", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Green-D" }) }) },
  { name: "/lines/Mattapan", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Mattapan" }) }) },
  { name: "/incidents", load: async () => (await import("@/app/incidents/page")).default() },
  { name: "/forecast", load: async () => (await import("@/app/forecast/page")).default() },
  { name: "/pipeline", load: async () => (await import("@/app/pipeline/page")).default() },
  { name: "/how-we-measure", load: async () => (await import("@/app/how-we-measure/page")).default() },
  { name: "/about", load: async () => (await import("@/app/about/page")).default() },
  { name: "/data", load: async () => (await import("@/app/data/page")).default() },
];

/** The layout's body children (header, main, footer, popover) as a fragment, for rendering in a div. */
export function Shell({ children }: { children: ReactNode }) {
  const tree = RootLayout({ children }) as ReactElement<{ children: ReactNode }>;
  const body = Children.toArray(tree.props.children).find(
    (c) => (c as ReactElement).type === "body",
  ) as ReactElement<{ children: ReactNode }>;
  return <div lang="en">{body.props.children}</div>;
}
