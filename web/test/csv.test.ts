import { describe, expect, it } from "vitest";
import { buildCsv, csvField, withheldReason, type CsvSpec } from "@/lib/csv";

const AT = "2026-11-24T12:00:00.000Z";
const base = (rows: CsvSpec["rows"]): CsvSpec => ({
  labelColumns: ["metric"],
  rows,
  definition: "8-headway-adherence-release-2",
  freshness: "2026-11-24T11:45:00Z",
});
const lines = (csv: string) => csv.trimEnd().split("\r\n");

describe("buildCsv", () => {
  it("starts with a comment carrying the definition URL and export time, then the header", () => {
    const out = lines(buildCsv(base([]), AT));
    expect(out[0]).toMatch(/^# metric definition: https:\/\/.+\/docs\/METRICS\.md#8-headway-adherence-release-2; exported 2026-11-24T12:00:00\.000Z$/);
    expect(out[1]).toBe("metric,value,unit,coverage_pct,withheld_reason,freshness");
  });

  it("writes a shown value with its coverage and freshness", () => {
    const out = lines(buildCsv(base([{ labels: { metric: "within band" }, cell: { value: 82.4, coverage_pct: 88.2 }, unit: "pct" }]), AT));
    expect(out[2]).toBe("within band,82.4,pct,88.2,,2026-11-24T11:45:00Z");
  });

  it("exports a withheld value as empty with the reason, never a number", () => {
    const out = lines(
      buildCsv(
        base([
          { labels: { metric: "a" }, cell: { value: 71, coverage_pct: 30, withheld_reason: "coverage 30% is below 50%" }, unit: "pct" },
          { labels: { metric: "b" }, cell: { value: 71, coverage_pct: 30 }, unit: "pct" },
          { labels: { metric: "c" }, cell: { value: null, coverage_pct: null }, unit: "s" },
        ]),
        AT,
      ),
    );
    expect(out[2]).toBe('a,,pct,30,coverage 30% is below 50%,2026-11-24T11:45:00Z');
    expect(out[3]).toMatch(/^b,,pct,30,coverage 30.0% is below/);
    expect(out[4]).toMatch(/^c,,s,,data unavailable,/);
    expect(out.slice(2).join("\n")).not.toContain("71");
  });

  it("keeps zero as a number and applies the 50% boundary", () => {
    const out = lines(
      buildCsv(
        base([
          { labels: { metric: "zero" }, cell: { value: 0, coverage_pct: 90 }, unit: "count" },
          { labels: { metric: "edge" }, cell: { value: 5, coverage_pct: 50 }, unit: "s" },
          { labels: { metric: "below" }, cell: { value: 5, coverage_pct: 49.9 }, unit: "s" },
        ]),
        AT,
      ),
    );
    expect(out[2]).toMatch(/^zero,0,count,90,,/);
    expect(out[3]).toMatch(/^edge,5,s,50,,/);
    expect(out[4]).toMatch(/^below,,s,49.9,/);
  });

  it("exports exactly the rows given, in order", () => {
    const rows = ["x", "y", "z"].map((m) => ({ labels: { metric: m }, cell: { value: 1, coverage_pct: 99 }, unit: "count" as const }));
    expect(lines(buildCsv(base(rows), AT)).slice(2).map((l) => l[0])).toEqual(["x", "y", "z"]);
    expect(lines(buildCsv(base(rows.slice(0, 1)), AT))).toHaveLength(3);
  });

  it("leaves freshness empty when unknown", () => {
    const out = lines(buildCsv({ ...base([{ labels: { metric: "m" }, cell: { value: 1, coverage_pct: 99 }, unit: "count" }]), freshness: null }, AT));
    expect(out[2]).toBe("m,1,count,99,,");
  });
});

describe("csvField", () => {
  it("quotes commas, quotes and newlines", () => {
    expect(csvField('a,"b"')).toBe('"a,""b"""');
    expect(csvField("l1\nl2")).toBe('"l1\nl2"');
  });
  it("neutralises formulas in text but not numbers", () => {
    expect(csvField("=SUM(A1)")).toBe("'=SUM(A1)");
    expect(csvField("@x")).toBe("'@x");
    expect(csvField(-3)).toBe("-3");
    expect(csvField(null)).toBe("");
  });
});

describe("withheldReason", () => {
  it("is empty for shown values", () => {
    expect(withheldReason({ value: 1, coverage_pct: 80 })).toBe("");
  });
});

describe("skipped-stop share unit", () => {
  it("the API fraction shows as percent on the page and exports as percent points", async () => {
    const { formatValue } = await import("@/lib/map");
    expect(formatValue(0.013, "share")).toBe("1.3%");
    const out = lines(buildCsv(base([{ labels: { metric: "skipped" }, cell: { value: 0.013, coverage_pct: 68 }, unit: "share" }]), AT));
    expect(out[2]).toBe("skipped,1.3,pct,68,,2026-11-24T11:45:00Z");
  });
  it("a withheld share keeps the percent unit", () => {
    const out = lines(buildCsv(base([{ labels: { metric: "skipped" }, cell: { value: null, coverage_pct: 30 }, unit: "share" }]), AT));
    expect(out[2]).toMatch(/^skipped,,pct,30,/);
  });
  it("the Orange fixture holds a fraction near 1.3%, not a percent", async () => {
    const { respond } = await import("../scripts/mock-api.mjs");
    const body = respond("/v1/lines/Orange/reliability", "").body as { data: { predicted_deviation: { rows: { skipped_share: number | null }[] } } };
    const rows = body.data.predicted_deviation.rows;
    for (const r of rows) if (r.skipped_share != null) expect(r.skipped_share).toBeLessThan(0.05);
  });
});
