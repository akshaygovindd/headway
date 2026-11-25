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
    expect(lines[0]).toMatch(/^# metric definition: .*METRICS\.md#.*; exported \d{4}-/);
    expect(lines[1]).toBe("line,section,metric,key,value,unit,coverage_pct,withheld_reason,freshness");
    const data = lines.slice(2);
    const segments = data.filter((l) => l.includes(",Segment health,"));
    expect(segments.length).toBeGreaterThan(20);
    expect(data.length - segments.length).toBeGreaterThan(0);
    expect(data.length).toBeGreaterThanOrEqual(shownCount());
    expect(segments[0]).toMatch(/,pct,\d+(\.\d+)?,,/);
  });

  it("exports withheld numbers as empty with a reason (Red fixture is 35% cov.)", async () => {
    const lines = await downloaded("/lines/Red (withheld, shuttle)");
    const data = lines.slice(2).filter((l) => !l.includes(",Segment health,"));
    expect(data.length).toBeGreaterThan(0);
    const withheld = data.filter((l) => l.includes("below 50%"));
    expect(withheld.length).toBeGreaterThan(0);
    for (const l of withheld) expect(l).toMatch(/,[a-z]+,35,coverage 35% is below 50%,/);
  });
});

describe("CSV on the Incidents page", () => {
  it("has one row per incident and withholds the low-coverage one", async () => {
    const lines = await downloaded("/incidents");
    expect(lines[0]).toContain("incidents-release-3");
    expect(lines).toHaveLength(2 + screen.getAllByRole("listitem").length);
    const i2 = lines.find((l) => l.startsWith("i2,"))!;
    expect(i2).toMatch(/,,h,35,/);
    expect(i2).toContain("below the minimum");
    expect(lines.find((l) => l.startsWith("i1,"))).toMatch(/,\d+,h,80,,/);
  });
});
