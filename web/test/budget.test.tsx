import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { currentBand, headlineBand } from "@/lib/linedata";
import { formatValue } from "@/lib/map";
import type { BudgetRow } from "@/lib/types";
import { PAGES, shell, stubFixtureApi } from "./helpers";

const row = (o: Partial<BudgetRow>): BudgetRow => ({
  direction_id: 0, time_band: "am_peak", n_trips: 30, n_scheduled_trips: 40, n_excluded_outage: 0, n_implausible: 0, n_disruption: 0,
  median_s: 600, p95_s: 900, buffer_time_index: 0.5, coverage_pct: 75, status: "measured", withheld_reason: null, no_trips_reason: null, ...o,
});

describe("budget helpers", () => {
  it("names the band of a moment in Eastern time", () => {
    expect(currentBand(new Date("2026-11-23T12:50:00Z"))).toBe("am_peak"); // 07:50 EST
    expect(currentBand(new Date("2026-11-23T17:00:00Z"))).toBe("midday"); // 12:00
    expect(currentBand(new Date("2026-11-23T21:00:00Z"))).toBe("pm_peak"); // 16:00
    expect(currentBand(new Date("2026-11-24T01:00:00Z"))).toBe("evening"); // 20:00 the evening before
    expect(currentBand(new Date("2026-11-24T08:00:00Z"))).toBe("other"); // 03:00
  });
  it("headlines the current band when measured, else the measured band with the most trips, else none", () => {
    const rows = [row({ time_band: "am_peak", n_trips: 30 }), row({ time_band: "midday", n_trips: 90 }), row({ time_band: "pm_peak", status: "withheld", median_s: null, p95_s: null, n_trips: 200 })];
    expect(headlineBand(rows, new Date("2026-11-23T12:50:00Z"))?.time_band).toBe("am_peak");
    expect(headlineBand(rows, new Date("2026-11-23T21:00:00Z"))?.time_band).toBe("midday"); // pm_peak is withheld
    expect(headlineBand([rows[2]!], new Date())).toBeNull();
  });
  it("rounds minutes to whole minutes", () => {
    expect(formatValue(840, "min0")).toBe("14 min");
    expect(formatValue(1260, "min0")).toBe("21 min");
  });
});

describe("budget pages", () => {
  beforeEach(() => {
    stubFixtureApi();
    vi.setSystemTime(new Date("2026-11-24T17:00:00Z")); // 12:00 Eastern, midday
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  const page = async (name: string) => {
    const el = await PAGES.find((p) => p.name === name)!.load();
    render(await shell(el));
  };

  it("answers usually X, leave Y for a measured pair, with n, coverage, window and a popover on each number", async () => {
    await page("/lines/Red/budget (measured pair)");
    const sentence = screen.getByTestId("budget-sentence");
    expect(sentence.textContent).toMatch(/^Usually/);
    expect(within(sentence).getAllByRole("button").map((b) => b.textContent)).toEqual(["8 min", "11 min"]); // midday: 500 s and 640 s (10.7 min)
    expect(screen.getByText(/58 trips measured of 64 scheduled \(91% coverage\)/)).toBeInTheDocument();
    expect(screen.getByText(/based on the last 7 days/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(within(sentence).getAllByRole("button")[1]!);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getAllByText(/95th percentile/i).length).toBeGreaterThan(0);
    expect(dialog.textContent).toMatch(/58/);
    expect(dialog.textContent).toMatch(/last 7 days/);
  });

  it("lists every band with its own state: measured, withheld by n, withheld by coverage, no trips", async () => {
    await page("/lines/Red/budget (measured pair)");
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => r.getAttribute("data-status"))).toEqual(["measured", "measured", "withheld", "no_trips"]);
    expect(rows[2]!.textContent).toMatch(/Not enough data/);
    expect(rows[2]!.textContent).not.toMatch(/min/); // no digits of a withheld value
    expect(within(rows[3]!).getByTestId("no-trips")).toBeInTheDocument();
  });

  it("shows no sentence and no numbers when every band is withheld", async () => {
    await page("/lines/Red/budget (every band withheld)");
    expect(screen.getByTestId("budget-none")).toBeInTheDocument();
    expect(screen.queryByTestId("budget-sentence")).toBeNull();
    expect(screen.getAllByText(/Not enough data/).length).toBeGreaterThanOrEqual(3);
  });

  it("explains a pair no trip serves instead of an error", async () => {
    await page("/lines/Red/budget (no trip between them)");
    expect(screen.getByTestId("budget-invalid")).toBeInTheDocument();
  });

  it("is a plain GET form on the line page (no client JavaScript) that posts to the budget page", async () => {
    const el = await (await import("@/app/lines/[line]/page")).default({ params: Promise.resolve({ line: "Red" }) });
    render(await shell(el));
    const form = screen.getByTestId("budget-form");
    expect(form.getAttribute("method")).toBe("get");
    expect(form.getAttribute("action")).toBe("/lines/Red/budget");
    expect(within(form).getByLabelText("From")).toBeRequired();
    expect(within(form).getByLabelText("To")).toBeRequired();
    expect(within(form).getAllByRole("option", { name: "Davis" })).toHaveLength(2);
  });
});
