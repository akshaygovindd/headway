import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PAGES, shell, stubFixtureApi } from "./helpers";

beforeEach(stubFixtureApi);
afterEach(() => vi.unstubAllGlobals());

async function open(name: string) {
  const page = PAGES.find((p) => p.name === name)!;
  render(await shell(await page.load()));
}

describe("line page", () => {
  it("has a 6px line-colour border, badge, termini, as-of and the window toggle", async () => {
    await open("/lines/Blue");
    const banner = screen.getByTestId("banner");
    expect(banner.style.borderBottom).toContain("6px solid");
    expect(banner.style.borderBottom).toContain("rgb(0, 61, 165)");
    expect(within(banner).getByRole("heading", { level: 1 })).toHaveTextContent("Blue Line");
    expect(banner).toHaveTextContent("Wonderland to Bowdoin");
    expect(banner).toHaveTextContent("as of 06:45 ET");
    const toggle = screen.getByRole("group", { name: "Time window" });
    expect(within(toggle).getByLabelText("24 h")).toBeChecked();
    expect(within(toggle).getByLabelText(/^7 d \(1 day available\)$/)).not.toBeChecked();
  });

  it("leads with spacing on heavy rail and with on-time on light rail", async () => {
    await open("/lines/Blue");
    const first = () => within(screen.getAllByTestId("key-numbers")[0]!).getAllByRole("term").map((t) => t.textContent);
    expect(first()).toEqual(["Evenly spaced", "Bunched · gaps", "On time (secondary)", "Countdown error, 0–5 min ahead"]);
    expect(within(screen.getAllByTestId("key-numbers")[0]!).getByRole("button", { name: "87%" })).toBeInTheDocument();
    expect(screen.getByTestId("line-note")).toHaveTextContent("about 4 minutes behind the timetable");
  });

  it("puts on time first on a Green branch", async () => {
    await open("/lines/Green-D");
    expect(within(screen.getAllByTestId("key-numbers")[0]!).getAllByRole("term")[0]).toHaveTextContent("On time");
  });

  it("shows Red as withheld with the disruption chip and dashed stretch data", async () => {
    await open("/lines/Red (withheld, shuttle)");
    const keys = within(screen.getAllByTestId("key-numbers")[0]!);
    expect(keys.getAllByTestId("withheld")[0]).toHaveTextContent("Not enough data · 35% cov.");
    expect(screen.getByTestId("banner")).toHaveTextContent("Service disruption");
    expect(document.querySelectorAll('[data-testid="station-strip"] [data-state="withheld"]').length).toBeGreaterThan(10);
    expect(document.querySelectorAll('[data-testid="station-strip"] .band-disruption').length).toBeGreaterThan(0);
  });

  it("marks Orange as partial", async () => {
    await open("/lines/Orange (partial)");
    const badges = within(screen.getAllByTestId("key-numbers")[0]!).getAllByTestId("coverage-badge");
    expect(badges[0]).toHaveAttribute("data-state", "partial");
    expect(badges[0]).toHaveTextContent("partial");
  });

  it("draws the Marey chart with annotations and a phone fallback", async () => {
    await open("/lines/Blue");
    const marey = screen.getByTestId("marey");
    expect(within(marey).getAllByRole("img").length).toBe(2);
    expect(marey.querySelector('[data-kind="observed"]')).not.toBeNull();
    expect(marey.querySelector('[data-kind="estimated"]')).not.toBeNull();
    expect(marey.querySelector('[data-kind="timetable"]')).not.toBeNull();
    expect(within(marey).getByRole("link", { name: /Open full chart/ })).toHaveAttribute("href", expect.stringMatching(/^#marey-full-/));
    expect(within(screen.getByTestId("marey-notes")).getAllByRole("listitem").length).toBeGreaterThan(0);
  });

  it("colours spacing by hour with the ramp and keeps outage, withheld and no-headway hours distinct", async () => {
    await open("/lines/Blue");
    const hours = screen.getByTestId("hour-strip");
    expect(hours.querySelectorAll("li")).toHaveLength(20);
    const first = hours.querySelectorAll("li")[0]!;
    expect(first).toHaveTextContent("05:00");
    expect(hours.querySelectorAll("li")[19]).toHaveTextContent("00:00");
    expect(first).toHaveAttribute("data-state", "even");
    const outage = hours.querySelectorAll('[data-state="outage"]');
    expect(outage).toHaveLength(1);
    expect(outage[0]).toHaveTextContent("21:00");
    expect(outage[0]).toHaveTextContent("our data gap");
    expect(outage[0]!.querySelector(".hatch-outage")).not.toBeNull();
    expect(hours.querySelectorAll('[data-state="withheld"]')).toHaveLength(1);
    expect(hours.querySelectorAll('[data-state="no_headways"]')).toHaveLength(1);
    expect(first).toHaveTextContent(/evenly spaced.*% within the band, 77% cov\./);
    expect(screen.getByTestId("hour-legend")).toHaveTextContent("Our data gap");
    expect(screen.getByTestId("hour-legend")).toHaveTextContent("No headways");
  });

  it("tells no trips, withheld and measured trip-time rows apart", async () => {
    await open("/lines/Blue");
    const rows = [...document.querySelectorAll('tr[data-status]')];
    expect(rows.some((r) => r.getAttribute("data-status") === "measured")).toBe(true);
    const none = screen.getAllByTestId("no-trips");
    expect(none).toHaveLength(2); // pm peak toward Wonderland, in both windows
    const measured = rows.find((r) => r.getAttribute("data-status") === "measured")!;
    expect(within(measured as HTMLElement).getAllByRole("button").length).toBe(4);
    expect(within(measured as HTMLElement).getAllByTestId("coverage-badge")[0]).toHaveTextContent("77% cov.");
  });

  it("shows Red trip time as withheld, never as no trips", async () => {
    await open("/lines/Red (withheld, shuttle)");
    expect(screen.queryByText("No trips measured yet")).toBeNull();
    const rows = [...document.querySelectorAll('tr[data-status="withheld"]')];
    expect(rows.length).toBeGreaterThan(0);
    expect(rows[0]).toHaveTextContent("Not enough data · 35% cov.");
    const hours = screen.getByTestId("hour-strip");
    expect(hours.querySelectorAll('[data-state="withheld"]').length).toBeGreaterThan(10);
  });

  it("puts the alert text in the banner chip", async () => {
    await open("/lines/Red (withheld, shuttle)");
    expect(screen.getByTestId("banner")).toHaveTextContent("Service disruption: Shuttle buses replace Red Line trains between Broadway and North Quincy");
  });

  it("Red: shuttle band, no train through it, both branches, observations only (withheld)", async () => {
    await open("/lines/Red (withheld, shuttle)");
    const charts = screen.getAllByTestId("marey");
    expect(charts).toHaveLength(2);
    const branchText = screen.getAllByTestId("marey-branch").map((b) => b.textContent);
    expect(branchText.some((t) => /Ashmont branch/.test(t ?? ""))).toBe(true);
    expect(branchText.some((t) => /Braintree branch/.test(t ?? ""))).toBe(true);
    for (const c of charts) {
      expect(within(c).getAllByTestId("shuttle-band")[0]).toHaveTextContent("Shuttle buses");
      // Every drawn segment ends outside the band: none starts before it and ends after it.
      const band = c.querySelector('[data-testid="shuttle-band"] rect')!;
      const y0 = Number(band.getAttribute("y"));
      const y1 = y0 + Number(band.getAttribute("height"));
      const d = (c.querySelector('[data-testid="marey-desktop"] [data-kind="observed"]') as SVGPathElement).getAttribute("d")!;
      for (const [, ya, yb] of d.matchAll(/M[\d.]+ ([\d.]+)L[\d.]+ ([\d.]+)/g)) {
        const [lo, hi] = [Number(ya), Number(yb)].sort((a, b) => a - b);
        expect(lo! >= y1 - 0.5 || hi! <= y0 + 0.5).toBe(true);
      }
      expect(within(c).getByTestId("marey-unscored")).toHaveTextContent("Observations only, not scored");
      expect(within(c).queryByTestId("marey-notes")).toBeNull();
    }
  });

  it("a shown line keeps its bunching and long-wait notes", async () => {
    await open("/lines/Blue");
    expect(screen.queryByTestId("marey-unscored")).toBeNull();
    expect(screen.queryByTestId("shuttle-band")).toBeNull();
  });

  it("countdown table lists only this line's stations, each with its own coverage", async () => {
    await open("/lines/Orange (partial)");
    const table = screen.getByRole("table", { name: /Countdown accuracy by station, Orange/ });
    expect(table).not.toHaveTextContent(/Davis|Alewife/);
    expect(table).toHaveTextContent("Malden Center");
  });

  it("Red keeps its row-level coverage rather than the line-wide figure", async () => {
    await open("/lines/Red (withheld, shuttle)");
    const table = screen.getByRole("table", { name: /Countdown accuracy by station, Red/ });
    const davis = within(table).getByText("Davis").closest("tr")!;
    expect(davis).toHaveTextContent("82% cov.");
    expect(within(table).getByText("Alewife").closest("tr")).toHaveTextContent("30% cov.");
  });
});

describe("station strip", () => {
  it("Green-C shows Cleveland Circle and North Station once each, at the stop points", async () => {
    const { Page } = await greenC();
    render(await shell(Page));
    const labels = screen.getAllByTestId("strip-labels")[0]!;
    const names = within(labels).getAllByTestId("strip-label").map((l) => l.textContent);
    expect(names.filter((n) => n === "Cleveland Circle")).toHaveLength(1);
    expect(names[0]).toBe("North Station");
    expect(names[names.length - 1]).toBe("Cleveland Circle");
    const segments = document.querySelectorAll('[data-testid="station-strip"]')[0]!.children.length;
    expect(names).toHaveLength(segments + 1);
  });
});

async function greenC() {
  const mod = await import("@/app/lines/[line]/page");
  return { Page: await mod.default({ params: Promise.resolve({ line: "Green-C" }) }) };
}
