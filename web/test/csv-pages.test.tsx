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
  it("has one data row per number shown, with the comment and header", async () => {
    const shownCount = () => screen.queryAllByTestId("metric").length;
    const lines = await downloaded("/lines/Red");
    expect(lines[0]).toMatch(/^# metric definition: .*METRICS\.md#.*; exported \d{4}-/);
    expect(lines[1]).toBe("line,section,metric,key,value,unit,coverage_pct,withheld_reason,freshness");
    expect(lines.length - 2).toBe(shownCount());
  });

  it("exports withheld numbers as empty with a reason (Blue fixture is under 50%)", async () => {
    const lines = await downloaded("/lines/Blue (withheld)");
    const data = lines.slice(2);
    expect(data.length).toBeGreaterThan(0);
    const withheld = data.filter((l) => l.includes("below 50%"));
    expect(withheld.length).toBeGreaterThan(0);
    for (const l of withheld) expect(l).toMatch(/,[a-z]+,30,coverage 30% is below 50%,/);
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
