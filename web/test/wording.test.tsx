import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { sortBuckets } from "@/lib/linedata";
import { checksText } from "@/lib/checks";
import { sloTitle } from "@/lib/definitions";
import { dateTimeET } from "@/lib/time";
import { fixture } from "./fixtures";
import { PAGES, shell, stubFixtureApi } from "./helpers";

beforeEach(stubFixtureApi);
afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
});

async function open(name: string) {
  const page = PAGES.find((p) => p.name === name)!;
  render(await shell(await page.load()));
}

describe("horizon buckets", () => {
  it("sort by number, not as text", () => {
    const rows = ["20-30", "5-10", "0-5", "10-20"].map((horizon_bucket) => ({ horizon_bucket }));
    expect(sortBuckets(rows).map((r) => r.horizon_bucket)).toEqual(["0-5", "5-10", "10-20", "20-30"]);
  });
  it("the line page shows them in that order even though the API sent them scrambled", async () => {
    await open("/lines/Blue");
    const table = screen.getByRole("table", { name: /Prediction accuracy by horizon/ });
    const firstCells = within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0]!.textContent);
    expect(firstCells).toEqual(["0-5", "5-10", "10-20", "20-30"]);
  });
});

describe("wording", () => {
  it("the steady trend names its threshold", async () => {
    await open("/lines/Blue");
    const text = screen.getByText(/Steady|Up |Down |Trend withheld|Data unavailable/).textContent ?? "";
    expect(text).not.toMatch(/within points/);
    if (text.startsWith("Steady")) expect(text).toContain("within 1 point of the 7-day figure");
  });

  it("the Bunched · gaps card labels its two values", async () => {
    await open("/lines/Blue");
    const keys = screen.getAllByTestId("key-numbers")[0]!;
    expect(within(within(keys).getByTestId("bunched-value")).getByText("Bunched")).toBeInTheDocument();
    expect(within(within(keys).getByTestId("gaps-value")).getByText("Gaps")).toBeInTheDocument();
  });

  it("says under 1 min, never 0 min, on the Marey notes", async () => {
    await open("/lines/Blue");
    expect(document.body.textContent?.match(/.{30}(^|[\s>])0(\.0)? min.{10}/)?.[0]).toBeUndefined();
  });

  it("uses singular and plural for warnings", () => {
    const c = { failing: 1, hours: 24, windowText: "last 24 h" };
    expect(checksText({ ...c, warnings: 1 })).toContain("1 warning (");
    expect(checksText({ ...c, warnings: 2 })).toContain("2 warnings (");
  });

  it("writes every time on the 24-hour clock", async () => {
    expect(dateTimeET("2026-11-24T13:00:00Z")).toBe("Nov 24, 2026, 08:00");
    expect(dateTimeET("2026-11-24T23:30:00Z")).toBe("Nov 24, 2026, 18:30");
    await open("/incidents");
    expect(document.body.textContent).not.toMatch(/\b\d{1,2}:\d{2}\s?(AM|PM)\b/i);
    expect(document.body.textContent).toMatch(/08:00/);
  });

  it("no band label uses AM or PM", async () => {
    await open("/lines/Blue");
    expect(document.body.textContent).not.toMatch(/\b(AM|PM)\b/);
  });

  it("title-cases SLO names", () => {
    expect(sloTitle("capture_completeness")).toBe("Capture Completeness");
    expect(sloTitle("gold_freshness")).toBe("Gold Freshness");
  });
});

describe("/pipeline", () => {
  it("lists every SLO, in title case, and its capture figure is the home strip figure", async () => {
    await open("/pipeline");
    const table = screen.getByRole("table", { name: "Service level objectives" });
    const names = new Set(within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[0]!.textContent));
    const slos = fixture("/v1/slo").data.slos as { slo_name: string }[];
    expect([...names].sort()).toEqual([...new Set(slos.map((s) => sloTitle(s.slo_name)))].sort());
    expect(names.size).toBeGreaterThan(1);
    const pipelineFigure = within(screen.getByTestId("capture-24h")).getByRole("button").textContent;
    cleanup();
    await open("/");
    const strip = screen.getByRole("region", { name: "Can you trust this page?" });
    const home = within(strip).getAllByTestId("own-metric")[0]!.textContent;
    expect(pipelineFigure).toBe(home);
  });
});

describe("popover figures come from the data", () => {
  it("each line's key numbers carry their own n, and a left-out count", async () => {
    const n = async (name: string) => {
      await open(name);
      const keys = screen.getAllByTestId("key-numbers")[0]!;
      const b = keys.querySelector('button[data-k="on_time"]') as HTMLElement;
      const w = keys.querySelector('button[data-k="within_band"]') as HTMLElement | null;
      const out = { on: b?.dataset.n, ex: (w ?? b)?.dataset.ex, within: w?.dataset.n };
      cleanup();
      return out;
    };
    const orange = await n("/lines/Orange");
    const blue = await n("/lines/Blue");
    expect(orange.within).toBeTruthy();
    expect(orange.within).not.toBe(blue.within);
    expect(orange.on).not.toBe(blue.on);
    expect(orange.ex).toMatch(/headways in hours when our own capture had a gap/);
  });
});
