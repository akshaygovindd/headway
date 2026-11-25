import { describe, expect, it } from "vitest";
import {
  coverageText,
  formatNumber,
  formatValue,
  freshnessText,
  GATE_TEXT,
  gateProgress,
  isHeavyRail,
  methodologyUrl,
  shown,
  trend,
  withheldText,
} from "@/lib/map";
import { failureFor } from "@/lib/api";

describe("shown", () => {
  it("shows a value at or above 50% coverage", () => {
    expect(shown({ value: 80, coverage_pct: 50 })).toEqual({ kind: "value", value: 80, coverage: 50 });
  });
  it("withholds below 50% even if the API sent a number", () => {
    expect(shown({ value: 80, coverage_pct: 49.9 }).kind).toBe("withheld");
  });
  it("withholds only low coverage or a stated reason; missing values are unavailable", () => {
    expect(shown({ value: null, coverage_pct: 40 }).kind).toBe("withheld");
    expect(shown({ value: 5, coverage_pct: 90, withheld_reason: "x" }).kind).toBe("withheld");
    expect(shown({ value: null, coverage_pct: 90 }).kind).toBe("unavailable");
    expect(shown({ value: 5, coverage_pct: null }).kind).toBe("unavailable");
    expect(shown({ value: null, coverage_pct: null }).kind).toBe("unavailable");
  });
  it("keeps zero as a value", () => {
    expect(shown({ value: 0, coverage_pct: 90 }).kind).toBe("value");
  });
});

describe("formatting", () => {
  it("rounds by unit", () => {
    expect(formatValue(61.5, "s")).toBe("62 s");
    expect(formatValue(87.456, "pct")).toBe("87.5%");
    expect(formatValue(0.614, "share")).toBe("61.4%");
    expect(formatValue(1.005, "ratio")).toBe("1.01");
    expect(formatNumber(-0.2, 0)).toBe("0");
    expect(formatNumber(12345, 0)).toBe("12,345");
  });
  it("words coverage and withheld text", () => {
    expect(coverageText(71.84)).toBe("coverage 71.8%");
    expect(coverageText(null)).toBe("coverage unknown");
    expect(withheldText(30)).toBe("Not enough data \u00b7 30% cov.");
    expect(withheldText(null)).toBe("Not enough data \u00b7 cov. unknown");
    expect(formatValue(12, "s20")).toBe("~20 s");
    expect(formatValue(20, "s20")).toBe("~20 s");
    expect(formatValue(31.4, "s20")).toBe("31 s");
  });
});

describe("freshnessText", () => {
  it.each([
    [null, "update time unknown"],
    [-3, "update time unknown"],
    [0.4, "updated just now"],
    [1, "updated 1 min ago"],
    [42.9, "updated 42 min ago"],
    [60, "updated 1 h ago"],
    [1528, "updated 25 h ago"],
    [3000, "updated 2 days ago"],
  ])("%s -> %s", (age, text) => {
    expect(freshnessText(age)).toBe(text);
  });
});

describe("trend", () => {
  it("is withheld unless both sides can be shown", () => {
    expect(trend({ value: 5, coverage_pct: 90 }, { value: null, coverage_pct: 20 }, 1).kind).toBe(
      "withheld",
    );
  });
  it("reports direction and flat band", () => {
    const d = { value: 80, coverage_pct: 90 };
    expect(trend(d, { value: 70, coverage_pct: 90 }, 1)).toEqual({ kind: "up", delta: 10 });
    expect(trend(d, { value: 90, coverage_pct: 90 }, 1)).toEqual({ kind: "down", delta: -10 });
    expect(trend(d, { value: 79.5, coverage_pct: 90 }, 1).kind).toBe("flat");
  });
});

describe("misc", () => {
  it("knows heavy rail", () => {
    expect(isHeavyRail("Red")).toBe(true);
    expect(isHeavyRail("Green-B")).toBe(false);
  });
  it("gate text and progress", () => {
    expect(GATE_TEXT).toBe("comparison unlocks after 21 service days incl. 3 weekends");
    expect(
      gateProgress({ n_days: 4, n_weekend_days: 1, min_days: 21, min_weekend_days: 3, gate_passed: false }),
    ).toBe("4 of 21 service days, 1 of 3 weekend days");
  });
  it("links to a METRICS.md anchor", () => {
    expect(methodologyUrl("docs/METRICS.md#4-freshness")).toMatch(/blob\/main\/docs\/METRICS\.md#4-freshness$/);
    expect(methodologyUrl("8-headway")).toMatch(/docs\/METRICS\.md#8-headway$/);
  });
});

describe("failureFor", () => {
  it("maps API errors", () => {
    expect(failureFor(404, { error: "not_available", reason: "agg_incidents is not in this export" })).toMatchObject({
      kind: "not_available",
    });
    expect(failureFor(404, {}).kind).toBe("not_found");
    expect(failureFor(422, {}).kind).toBe("invalid");
    expect(failureFor(502, {}).kind).toBe("unavailable");
    expect(failureFor(504, null).kind).toBe("unavailable");
  });
});
