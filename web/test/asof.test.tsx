import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { respond } from "../scripts/mock-api.mjs";
import { PAGES, shell, stubFixtureApi } from "./helpers";

beforeEach(stubFixtureApi);
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

describe("one shared as-of from /v1/meta", () => {
  it("every page shows the same single as-of line", async () => {
    const seen = new Set<string>();
    for (const page of PAGES) {
      render(await shell(await page.load()));
      const el = screen.getAllByTestId("as-of");
      expect(el, page.name).toHaveLength(1);
      expect(el[0]).toHaveTextContent("Data as of 06:45 ET");
      seen.add(el[0]!.textContent!.replace(/exported.*|updated.*/, ""));
      cleanup();
    }
    expect(seen.size).toBe(1);
  });

  it("says data unavailable, not a stale time, when /meta fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url);
        if (url.pathname === "/v1/meta") return new Response("{}", { status: 503 });
        const { status, body } = respond(url.pathname, url.search);
        return new Response(JSON.stringify(body), { status });
      }),
    );
    render(await shell(<p>x</p>));
    expect(screen.getByTestId("as-of")).toHaveTextContent("Data unavailable right now");
    expect(screen.getByTestId("as-of")).not.toHaveTextContent(/too little|coverage/i);
  });
});

describe("the freshness is not frozen in the built page", () => {
  it("server markup carries the export clock time, never an age", async () => {
    const { renderToString } = await import("react-dom/server");
    const html = renderToString(await shell(<p>x</p>));
    expect(html).not.toMatch(/\d+ min ago|\d+ h ago|just now/);
    expect(html).toContain("exported ");
  });
});
