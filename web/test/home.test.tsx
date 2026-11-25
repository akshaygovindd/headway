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
