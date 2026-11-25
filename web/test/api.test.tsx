import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorState } from "@/components/ui";
import { getApi } from "@/lib/api";

afterEach(() => vi.unstubAllGlobals());

describe("getApi", () => {
  it("returns a failure, not a throw, when the API is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const r = await getApi("/lines");
    expect(r).toMatchObject({ ok: false, kind: "unavailable" });
  });

  it("maps a not_available 404 and an unreadable 502", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(JSON.stringify({ error: "not_available", reason: "x" }), { status: 404 })),
    );
    expect(await getApi("/incidents")).toMatchObject({ ok: false, kind: "not_available" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("bad gateway", { status: 502 })));
    expect(await getApi("/lines")).toMatchObject({ ok: false, kind: "unavailable", status: 502 });
  });

  it("returns the envelope on success", async () => {
    const body = { data: [1], coverage: {}, freshness: {}, provenance: {} };
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 200 })));
    expect(await getApi("/lines")).toEqual({ ok: true, body });
  });
});

describe("ErrorState", () => {
  it("shows a friendly message when the service is down", () => {
    render(<ErrorState failure={{ ok: false, kind: "unavailable", status: null, message: "x" }} />);
    expect(screen.getByRole("alert")).toHaveTextContent("We can't reach the data service right now.");
  });

  it("says not published yet for a missing table", () => {
    render(<ErrorState failure={{ ok: false, kind: "not_available", status: 404, message: "x" }} what="Incidents" />);
    expect(screen.getByRole("status")).toHaveTextContent("Incidents is not published yet.");
  });
});
