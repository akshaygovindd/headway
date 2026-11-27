import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PAGES, stubFixtureApi } from "./helpers";

let saved: Blob | null = null;
beforeEach(() => {
  stubFixtureApi();
  saved = null;
  URL.createObjectURL = vi.fn((b: Blob) => ((saved = b), "blob:x"));
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function downloaded(name: string) {
  const page = PAGES.find((p) => p.name === name)!;
  render(await page.load());
  fireEvent.click(screen.getByRole("button", { name: "Download CSV" }));
  await waitFor(() => expect(saved).not.toBeNull());
  const text = await new Promise<string>((res) => {
    const fr = new FileReader();
    fr.onload = () => res(String(fr.result));
    fr.readAsText(saved!);
  });
  return text.trimEnd().split("\r\n");
}

describe("CSV on the Line page", () => {
  it("has a data row for every number shown plus the segment rows, with the comment and header", async () => {
    const shownCount = () => screen.queryAllByTestId("metric").length;
    const lines = await downloaded("/lines/Blue");
    expect(lines[0]).toBe("line,section,metric,key,value,unit,coverage_pct,withheld_reason,freshness");
    expect(lines.some((l) => l.startsWith("#"))).toBe(false);
    const data = lines.slice(1);
    const segments = data.filter((l) => l.includes(",Segment health,"));
    expect(segments.length).toBeGreaterThan(20);
    expect(data.length - segments.length).toBeGreaterThan(0);
    expect(data.length).toBeGreaterThanOrEqual(shownCount());
    expect(segments[0]).toMatch(/,pct,\d+(\.\d+)?,,/);
  });

  it("exports withheld numbers as empty with a reason (Red fixture is 35% cov.)", async () => {
    const lines = await downloaded("/lines/Red (withheld, shuttle)");
    const data = lines.slice(1).filter((l) => !l.includes(",Segment health,") && !l.includes(",Countdown accuracy,") && !l.includes(",Spacing by hour,") && !l.includes(",Trip time"));
    expect(data.length).toBeGreaterThan(0);
    const withheld = data.filter((l) => l.includes("below 50%"));
    expect(withheld.length).toBeGreaterThan(0);
    for (const l of withheld) expect(l).toMatch(/,[a-z]+,35,coverage 35% is below 50%,/);
  });
});

describe("CSV completeness", () => {
  it("keeps bands with no trips as rows with a reason", async () => {
    const lines = await downloaded("/lines/Orange");
    const trip = lines.filter((l) => l.includes(",Trip time against the plan,") && l.includes("Afternoon peak") && l.includes("Toward"));
    const noTrips = trip.filter((l) => l.includes("scheduled trips were not observed"));
    expect(noTrips).toHaveLength(8); // 4 measures x 2 windows
    for (const l of noTrips) expect(l).toMatch(/,,s,/);
  });

  it("exports spacing by hour, outage hours included, each with its reason", async () => {
    const lines = await downloaded("/lines/Red (withheld, shuttle)");
    const hours = lines.filter((l) => l.includes(",Spacing by hour,"));
    expect(hours.length).toBeGreaterThanOrEqual(19);
    const outage = hours.filter((l) => l.includes("our data gap"));
    expect(outage.length).toBeGreaterThan(0);
    for (const l of outage) expect(l).toMatch(/,,pct,/);
  });
});

describe("CSV agrees with the page on countdown rows", () => {
  it("each station row exports its own coverage and reason, as the page shows them", async () => {
    const lines = await downloaded("/lines/Red (withheld, shuttle)");
    const cd = lines.filter((l) => l.includes(",Countdown accuracy,"));
    const davis = cd.filter((l) => l.includes("Davis"));
    const alewife = cd.filter((l) => l.includes("Alewife"));
    expect(davis.length).toBe(2);
    for (const l of davis) expect(l).toMatch(/,pct,82,,/);
    for (const l of alewife) expect(l).toMatch(/,,pct,30,coverage 30% is below 50%,/);
    const table = screen.getByRole("table", { name: /Countdown accuracy by station, Red/ });
    expect(table).toHaveTextContent("82% cov.");
    expect(table).toHaveTextContent("30% cov.");
  });
});

describe("CSV on the Incidents page", () => {
  it("has one row per incident and withholds the low-coverage one", async () => {
    const lines = await downloaded("/incidents");
    expect(lines[0]).toMatch(/^incident_id,line,start,end,/);
    expect(lines).toHaveLength(1 + screen.getAllByRole("listitem").length);
    const i3 = lines.find((l) => l.startsWith("i3,"))!;
    expect(i3).toMatch(/,,h,42,/);
    expect(i3).toContain("below the minimum");
    // Coverage is the incident's own mean (what the page judges it by), not its worst hour.
    expect(lines.find((l) => l.startsWith("i1,"))).toMatch(/,\d+,h,84,,/);
  });
});
