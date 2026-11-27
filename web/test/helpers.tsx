import { Children, cloneElement, isValidElement, type ReactElement, type ReactNode } from "react";
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
  { name: "/lines/Orange", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Orange" }) }) },
  { name: "/lines/Blue", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Blue" }) }) },
  { name: "/lines/Green-D", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Green-D" }) }) },
  { name: "/lines/Mattapan", load: async () => (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Mattapan" }) }) },
  { name: "/lines/Red/budget (measured pair)", load: async () => (await import("@/app/lines/[line]/budget/page")).default({ params: Promise.resolve({ line: "Red" }), searchParams: Promise.resolve({ from: "place-davis", to: "place-harvard" }) }) },
  { name: "/lines/Red/budget (every band withheld)", load: async () => (await import("@/app/lines/[line]/budget/page")).default({ params: Promise.resolve({ line: "Red" }), searchParams: Promise.resolve({ from: "place-alewife", to: "place-braintree", window: "last_24h" }) }) },
  { name: "/lines/Red/budget (no trip between them)", load: async () => (await import("@/app/lines/[line]/budget/page")).default({ params: Promise.resolve({ line: "Red" }), searchParams: Promise.resolve({ from: "place-braintree", to: "place-davis" }) }) },
  { name: "/accessibility", load: async () => (await import("@/app/accessibility/page")).default() },
  { name: "/slow-zones", load: async () => (await import("@/app/slow-zones/page")).default() },
  { name: "/incidents", load: async () => (await import("@/app/incidents/page")).default() },
  { name: "/forecast", load: async () => (await import("@/app/forecast/page")).default() },
  { name: "/pipeline", load: async () => (await import("@/app/pipeline/page")).default() },
  { name: "/how-we-measure", load: async () => (await import("@/app/how-we-measure/page")).default() },
  { name: "/about", load: async () => (await import("@/app/about/page")).default() },
  { name: "/data", load: async () => (await import("@/app/data/page")).default() },
];

/** Evaluate async server components (the layout's "as of") so jsdom can render the tree. */
async function resolve(node: ReactNode): Promise<ReactNode> {
  if (Array.isArray(node)) return Promise.all(node.map(resolve));
  if (!isValidElement(node)) return node;
  const el = node as ReactElement<{ children?: ReactNode }>;
  if (typeof el.type === "function" && el.type.constructor.name === "AsyncFunction") {
    return resolve(await (el.type as (p: unknown) => Promise<ReactNode>)(el.props));
  }
  const kids = el.props.children;
  if (kids == null || typeof kids === "string") return el;
  const done = Array.isArray(kids) ? await Promise.all(kids.map(resolve)) : [await resolve(kids)];
  return cloneElement(el, undefined, ...done);
}

/** The layout's body children (header, main, footer, popover), with async parts resolved, for rendering in a div. */
export async function shell(children: ReactNode): Promise<ReactElement> {
  const tree = (await RootLayout({ children })) as ReactElement<{ children: ReactNode }>;
  const body = Children.toArray(tree.props.children).find(
    (c) => (c as ReactElement).type === "body",
  ) as ReactElement<{ children: ReactNode }>;
  return <div lang="en">{await resolve(body.props.children)}</div>;
}
