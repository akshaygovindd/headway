import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { readFileSync } from "node:fs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { respond } from "../scripts/mock-api.mjs";
import { shell, stubFixtureApi } from "./helpers";

const page = async () => {
  const el = await (await import("@/app/slow-zones/page")).default();
  render(await shell(el));
};

/** Serve the fixtures, but the slow-zones body comes from `slow(body)`. */
function stubSlowZones(slow: (body: ReturnType<typeof load>) => void) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
      if (url.pathname === "/v1/slow-zones") {
        const body = load();
        slow(body);
        return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
      }
      const { status, body } = respond(url.pathname, url.search);
      return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
    }),
  );
}
const load = () => JSON.parse(readFileSync(`${process.cwd()}/test/fixtures/slow-zones_not-captured.json`, "utf8"));

afterEach(() => vi.unstubAllGlobals());

describe("/slow-zones with a captured snapshot (fixture rows, not MBTA data)", () => {
  beforeEach(() => stubFixtureApi());

  it("says what the official list is: a dated snapshot, with its source", async () => {
    await page();
    const box = screen.getByTestId("official-provenance");
    expect(box).toHaveAttribute("data-status", "captured");
    expect(box.textContent).toMatch(/Snapshot of the MBTA's list, captured 2026-11-20/);
    expect(box.textContent).toMatch(/not a live feed/);
    expect(within(box).getByRole("link")).toHaveAttribute("href", "https://example.invalid/fixture-not-mbta-data");
  });

  it("calls the observed flags a signal, not the official list", async () => {
    await page();
    expect(screen.getByText(/observed speed-restriction signal, not the MBTA's official list/)).toBeInTheDocument();
  });

  it("counts the four states", async () => {
    await page();
    const n = (k: string) => screen.getByTestId(`count-${k}`).textContent;
    expect(n("matched")).toMatch(/^1/);
    expect(n("observed_only")).toMatch(/^2/);
    expect(n("listed_only")).toMatch(/^1/);
    expect(n("listed_unmapped")).toMatch(/^1/);
  });

  it("lists matched, observed-only, listed-only and unmatched rows in words", async () => {
    await page();
    const rows = within(screen.getByRole("table", { name: /compared with the MBTA's list/ })).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["matched", "observed_only", "observed_only", "listed_only", "listed_unmapped"]);
    expect(rows[0]!.textContent).toMatch(/On the official list and slow here/);
    expect(rows[0]!.textContent).toMatch(/10 mph/);
    expect(rows[3]!.textContent).toMatch(/On the official list, not slow here/);
    expect(rows[3]!.textContent).toMatch(/Not slow/);
    expect(rows[4]!.textContent).toMatch(/Somewhere between two stops \(FIXTURE\)/);
  });

  it("withholds a flagged segment with too little coverage: hatch, reason, no digits", async () => {
    await page();
    const row = within(screen.getByRole("table", { name: /Slow segments seen in the data/ })).getAllByRole("row").find((r) => r.getAttribute("data-status") === "withheld")!;
    expect(row.textContent).toMatch(/Not enough data/);
    expect(row.textContent).not.toMatch(/\d+ s\b/);
    expect(row.textContent).toMatch(/Kendall/);
  });

  it("draws a bar per segment for lines that have a flag, marked by severity, and describes each in words", async () => {
    await page();
    const lines = screen.getAllByTestId("slow-line").map((l) => l.getAttribute("data-line"));
    expect(lines).toEqual(["Red", "Orange"]);
    const flags = within(screen.getAllByTestId("slow-line")[0]!).getAllByRole("listitem").map((li) => li.getAttribute("data-flag")).filter((f) => f !== "none");
    expect(flags).toEqual(["minor", "moderate", "withheld"]);
    expect(screen.getAllByText(/Porter to Harvard: moderate slow segment/)[0]).toBeInTheDocument();
  });

  it("every number is a popover button that names its definition, n and window", async () => {
    await page();
    const btn = within(screen.getByRole("table", { name: /Slow segments seen/ })).getAllByRole("button")[0]!;
    expect(btn.getAttribute("data-k")).toBe("slow_segment");
    expect(btn.getAttribute("data-n")).toBe("22");
    expect(btn.getAttribute("data-win")).toBe("last_7d");
  });
});

describe("/slow-zones before the official list is captured (the committed state)", () => {
  beforeEach(() => stubSlowZones(() => {}));

  it("says official list not captured yet and never says a segment is missing from the list", async () => {
    await page();
    const box = screen.getByTestId("official-provenance");
    expect(box).toHaveAttribute("data-status", "not_captured");
    expect(box.textContent).toMatch(/Official list not captured yet/);
    expect(box.textContent).toMatch(/nothing can be said about whether it is on the official list/);
    const table = screen.getByRole("table", { name: /compared with the MBTA's list/ });
    expect(within(table).queryByText(/On the official list and slow here/)).toBeNull();
    expect(within(table).queryByText(/On the official list, not slow here/)).toBeNull();
    expect(screen.getByTestId("count-listed_only").textContent).toMatch(/^0/);
    expect(screen.getByTestId("count-matched").textContent).toMatch(/^0/);
  });

  it("shows every flagged segment as slow in the data, with no listed-only or unmatched rows", async () => {
    await page();
    const rows = within(screen.getByRole("table", { name: /compared with the MBTA's list/ })).getAllByRole("row").slice(1);
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((r) => r.getAttribute("data-status") === "observed_only")).toBe(true);
  });
});

describe("/slow-zones accessibility in the not-captured state", () => {
  it("has no axe violations", async () => {
    HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
    stubSlowZones(() => {});
    const el = await (await import("@/app/slow-zones/page")).default();
    const { container } = render(await shell(el));
    const r = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
});

describe("/slow-zones when nothing is slow", () => {
  it("says so instead of drawing an empty diagram", async () => {
    stubSlowZones((body) => {
      body.data.segments = [];
      body.data.comparison = [];
      body.data.counts = { matched: 0, observed_only: 0, listed_only: 0, listed_unmapped: 0 };
    });
    await page();
    expect(screen.getByTestId("no-slow")).toBeInTheDocument();
    expect(screen.getByTestId("no-comparison").textContent).toMatch(/Nothing to compare yet/);
    expect(screen.queryAllByTestId("slow-line")).toHaveLength(0);
  });
});
