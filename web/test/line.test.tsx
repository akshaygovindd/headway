import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PAGES, Shell, stubFixtureApi } from "./helpers";

beforeEach(stubFixtureApi);
afterEach(() => vi.unstubAllGlobals());

async function open(name: string) {
  const page = PAGES.find((p) => p.name === name)!;
  render(<Shell>{await page.load()}</Shell>);
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
    expect(within(marey).getByRole("link", { name: /Open full chart/ })).toHaveAttribute("href", "#marey-full");
    expect(within(screen.getByTestId("marey-notes")).getAllByRole("listitem").length).toBeGreaterThan(0);
  });

  it("hatches the outage hour and says no trips are measured yet", async () => {
    await open("/lines/Blue");
    const hours = screen.getByTestId("hour-strip");
    const outage = hours.querySelectorAll('[data-state="outage"]');
    expect(outage).toHaveLength(1);
    expect(outage[0]).toHaveTextContent("21:00");
    expect(outage[0]).toHaveTextContent("our data gap");
    expect(screen.getAllByText("No trips measured yet")).toHaveLength(2);
  });
});
