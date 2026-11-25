import { describe, expect, it } from "vitest";
import { healthBucket, segmentState } from "@/lib/health";
import { disruptedStretches, orderedSegments, stateCounts, termini } from "@/lib/network";
import type { SegmentRow } from "@/lib/types";

describe("health bucketing", () => {
  it.each([
    [100, "even"],
    [80, "even"],
    [79.9, "some_bunching"],
    [60, "some_bunching"],
    [59.9, "bunched_gaps"],
    [0, "bunched_gaps"],
    [null, null],
  ] as const)("%s is %s", (pct, bucket) => {
    expect(healthBucket(pct)).toBe(bucket);
  });

  it("withheld wins over a bucket, and a missing bucket is computed", () => {
    expect(segmentState({ health: "even", pct_within_band: 90, withheld_reason: "coverage 30% is below 50%" })).toBe("withheld");
    expect(segmentState({ health: null, pct_within_band: 65, withheld_reason: null })).toBe("some_bunching");
    expect(segmentState({ health: null, pct_within_band: null, withheld_reason: null })).toBe("withheld");
  });
});

const seg = (order: number, over: Partial<SegmentRow> = {}): SegmentRow => ({
  direction_id: 0, from_stop_id: `s${order}`, from_stop_name: `S${order}`, to_stop_id: `s${order + 1}`, to_stop_name: `S${order + 1}`,
  segment_order: order, health: "even", pct_within_band: 90, pct_bunched: 5, pct_gap: 5, n_headways: 10, n_excluded_outage: 0,
  disrupted: false, coverage_pct: 80, withheld_reason: null, ...over,
});

describe("network rows", () => {
  it("orders by position, keeps one segment per position and one direction", () => {
    const segs = orderedSegments([seg(3), seg(1), seg(2), seg(2, { from_stop_id: "branch" }), seg(1, { direction_id: 1 })]);
    expect(segs.map((s) => s.row.segment_order)).toEqual([1, 2, 3]);
    expect(segs[1]?.row.from_stop_id).toBe("s2");
    expect(termini(segs)).toEqual(["S1", "S4"]);
  });

  it("finds runs of disrupted segments", () => {
    const segs = orderedSegments([
      seg(1), seg(2, { disrupted: true }), seg(3, { disrupted: true }), seg(4), seg(5, { disrupted: true }),
    ]);
    expect(disruptedStretches(segs)).toEqual([
      { from: "S2", to: "S4", start: 1, end: 2 },
      { from: "S5", to: "S6", start: 4, end: 4 },
    ]);
  });

  it("counts states", () => {
    const segs = orderedSegments([seg(1), seg(2, { health: null, pct_within_band: null, withheld_reason: "x" })]);
    expect(stateCounts(segs)).toEqual({ even: 1, some_bunching: 0, bunched_gaps: 0, withheld: 1 });
  });
});
