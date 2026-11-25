import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PAGES, shell, stubFixtureApi } from "./helpers";

beforeEach(stubFixtureApi);
afterEach(() => vi.unstubAllGlobals());

async function home() {
  const page = PAGES.find((p) => p.name === "/")!;
  render(await shell(await page.load()));
}

describe("home page", () => {
  it("opens with the eyebrow and the generated sentence from the fixtures", async () => {
    await home();
    expect(screen.getByTestId("eyebrow")).toHaveTextContent("Boston subway · last 24 hours · as of 06:45 ET");
    const hero = screen.getByTestId("hero").textContent ?? "";
    expect(hero).toMatch(/^Right now, 7 of 8 lines can be measured\. Heavy-rail trains were evenly spaced \d+–\d+% of the time; Green Line branches and Mattapan were on time \d+–\d+% of the time\./);
    expect(hero).toMatch(/Red withheld \(35% coverage during a service disruption\)\.$/);
    expect(screen.getByRole("heading", { level: 1 })).toBe(screen.getByTestId("hero"));
  });

  it("lists the lines in fixed order with Red withheld, Orange partial and Blue shown", async () => {
    await home();
    const cards = within(screen.getByRole("list", { name: "Lines" })).getAllByRole("listitem");
    expect(cards.map((c) => within(c).getByRole("heading").textContent)).toEqual([
      "Red Line", "Orange Line", "Blue Line", "Green Line B", "Green Line C", "Green Line D", "Green Line E", "Mattapan Trolley",
    ]);
    expect(within(cards[0]!).getByTestId("withheld")).toHaveTextContent("Not enough data · 35% cov.");
    const chip = within(cards[0]!).getByTestId("disruption");
    expect(chip).toHaveTextContent("Service disruption: Shuttle buses replace Red Line trains between Broadway and North Quincy");
    expect(chip).toHaveAttribute("title", chip.textContent ?? "");
    // Alert text is available, so nothing is inferred from incidents.
    expect(within(cards[0]!).queryByTestId("skipped")).toBeNull();
    expect(within(cards[1]!).getByTestId("coverage-badge")).toHaveAttribute("data-state", "partial");
    expect(within(cards[1]!).getByTestId("coverage-badge")).toHaveTextContent("68% cov.");
    expect(within(cards[2]!).getByRole("button", { name: "87%" })).toBeInTheDocument();
    expect(within(cards[2]!).getByTestId("coverage-badge")).toHaveTextContent("77% cov.");
  });

  it("draws a dashed disruption stretch on Red and hatches its withheld segments", async () => {
    await home();
    const stretches = screen.getAllByTestId("stretch");
    expect(stretches).toHaveLength(1);
    const label = screen.getByTestId("stretch-label");
    expect(label).toHaveTextContent("Service disruption: Shuttle buses replace Red Line trains between Broadway and North Quincy");
    expect(label).toHaveAttribute("title", label.textContent ?? "");
    expect(document.querySelectorAll('[data-testid="network-row"] [data-state="withheld"]').length).toBeGreaterThan(10);
    expect(document.querySelectorAll('[data-testid="network-row"] [data-state="even"]').length).toBeGreaterThan(10);
  });

  it("shows the trust strip with the outage hour marked as ours", async () => {
    await home();
    const strip = screen.getByRole("region", { name: "Can you trust this page?" });
    expect(strip).toHaveTextContent("06:45 ET");
    expect(strip).toHaveTextContent("our data, not the trains");
    expect(strip.querySelector('[data-testid="checks"]')).toHaveTextContent("1 failing · 1 warnings");
    expect(strip.querySelector('[data-testid="checks"]')).toHaveTextContent("last 24 h");
    expect(strip.querySelectorAll('[data-outage="true"]')).toHaveLength(1);
  });

  it("plots accuracy with withheld Red hatched and sub-floor values as ~20 s", async () => {
    await home();
    expect(screen.getAllByTestId("accuracy-withheld")).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "~20 s" }).length).toBeGreaterThan(0);
  });

  it("explains the five states", async () => {
    await home();
    const key = screen.getByTestId("state-key");
    for (const name of ["Shown", "Partial", "Withheld", "Our data gap", "Service disruption"]) expect(key).toHaveTextContent(name);
  });
});

describe("home page without the new endpoints", () => {
  it("shows checks n/a and falls back to the segment header, then to the inferred skip label", async () => {
    const real = (await import("../scripts/mock-api.mjs")).respond;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
        const gone = url.pathname === "/v1/pipeline/checks" || url.pathname.endsWith("/disruptions");
        const { status, body } = gone ? { status: 404, body: { error: "not_available", reason: "not in this export" } } : real(url.pathname, url.search);
        return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      }),
    );
    await home();
    expect(screen.getByTestId("checks")).toHaveTextContent("n/a");
    const cards = within(screen.getByRole("list", { name: "Lines" })).getAllByRole("listitem");
    expect(within(cards[0]!).getByTestId("disruption")).toHaveTextContent("Shuttle buses replace Red Line trains");
  });
});

describe("pages when the API has nothing", () => {
  it("/forecast shows an error state per line instead of waiting on data", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: "not_found" }), { status: 404 })));
    const page = PAGES.find((p) => p.name === "/forecast")!;
    render(await shell(await page.load()));
    expect(screen.getAllByTestId("error-state").length).toBe(8);
    expect(screen.queryByText("Loading")).toBeNull();
  });

  it("/data lists the contracts without the API", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 503 })));
    const page = PAGES.find((p) => p.name === "/data")!;
    render(await shell(await page.load()));
    expect(screen.getByRole("heading", { level: 1 })).toBeInTheDocument();
    expect(screen.queryByText("Loading")).toBeNull();
  });
});

describe("data outages are never incidents", () => {
  it("the guard drops outage-only incidents and the page does not list them", async () => {
    const { isRealIncident } = await import("@/lib/site-data");
    const base = { incident_id: "x", line: "Red", direction_id: 0, start_ts: "2026-11-24T14:00:00-05:00", end_ts: "2026-11-24T16:00:00-05:00", n_hours: 2, is_open: false, thresholds_crossed: ["skipped"], min_coverage_pct: 60, mean_coverage_pct: 60, coverage_pct: 60, n_outage_hours_inside: 0, match_status: null, alert_lag_s: null, summary_text: "x", template_used: true };
    expect(isRealIncident(base)).toBe(true);
    expect(isRealIncident({ ...base, n_outage_hours_inside: 2 })).toBe(false); // outage hours counted as incident hours
    expect(isRealIncident({ ...base, thresholds_crossed: ["data_outage"] })).toBe(false);
    expect(isRealIncident({ ...base, n_hours: 1, end_ts: "2026-11-24T15:00:00-05:00" })).toBe(false);
    expect(isRealIncident({ ...base, end_ts: null, n_outage_hours_inside: 3 })).toBe(true);
  });

  it("a poisoned API response never reaches the Incidents page", async () => {
    const { respond } = await import("../scripts/mock-api.mjs");
    vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
      const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : (input as Request).url);
      const { status, body } = respond(url.pathname, url.search);
      if (url.pathname === "/v1/incidents") {
        const b = JSON.parse(JSON.stringify(body));
        b.data.incidents.push({ ...b.data.incidents[0], incident_id: "outage-1", n_hours: 2, end_ts: "2026-11-24T10:00:00-05:00", start_ts: "2026-11-24T08:00:00-05:00", n_outage_hours_inside: 2, thresholds_crossed: ["skipped"] });
        return new Response(JSON.stringify(b), { status });
      }
      return new Response(JSON.stringify(body), { status });
    }));
    const page = PAGES.find((p) => p.name === "/incidents")!;
    render(await shell(await page.load()));
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(3); // i1, i2, i3 only
  });

  it("the incidents fixture is internally consistent: evaluated plus outage hours fit the span", async () => {
    const { respond } = await import("../scripts/mock-api.mjs");
    const { isRealIncident } = await import("@/lib/site-data");
    const body = respond("/v1/incidents", "").body as { data: { incidents: Parameters<typeof isRealIncident>[0][] } };
    for (const i of body.data.incidents) expect(isRealIncident(i), i.incident_id).toBe(true);
  });
});
