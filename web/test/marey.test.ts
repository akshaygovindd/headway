import { describe, expect, it } from "vitest";
import { annotateMarey, layoutMarey } from "@/lib/marey";
import type { MareyRow } from "@/lib/types";

const T0 = Date.parse("2026-11-24T10:00:00Z");
const row = (trip: string, stop: string, seq: number, min: number, method: MareyRow["method"] = "observed"): MareyRow => ({
  direction_id: 0, trip_id: trip, stop_id: stop, stop_name: stop.toUpperCase(), stop_sequence: seq,
  arrival_ts: new Date(T0 + min * 60000).toISOString().replace(".000Z", "Z"), method, confidence: 0.9, scheduled_ts: null,
});

describe("annotateMarey", () => {
  it("flags trains much closer than usual and long waits, per stop", () => {
    // arrivals at stop a: 0, 8, 10 (bunched after 8), 30 (long wait); median gap 8... gaps 8,2,20
    const rows = [0, 8, 10, 30].map((m, i) => row(`t${i}`, "a", 1, m));
    const a = annotateMarey(rows);
    expect(a.map((x) => x.kind)).toEqual(["bunching", "long_wait"]);
    expect(a[0]).toMatchObject({ stop_name: "A", gap_s: 120, expected_s: 480 });
    expect(a[1]).toMatchObject({ gap_s: 1200 });
  });

  it("needs three trains at a stop and ignores evenly spaced service", () => {
    expect(annotateMarey([row("t0", "a", 1, 0), row("t1", "a", 1, 1)])).toEqual([]);
    expect(annotateMarey([0, 10, 20, 30].map((m, i) => row(`t${i}`, "a", 1, m)))).toEqual([]);
  });

  it("keeps only the worst per kind", () => {
    const rows = ["a", "b", "c"].flatMap((s) => [0, 10, 11, 20, 30].map((m, i) => row(`t${i}`, s, 1, m)));
    expect(annotateMarey(rows, 2).filter((x) => x.kind === "bunching")).toHaveLength(2);
  });
});

describe("layoutMarey", () => {
  const rows = [row("t0", "a", 1, 0), row("t0", "b", 2, 10, "estimated"), row("t1", "a", 1, 30), row("t1", "b", 2, 40)];
  const opts = { t0: T0, t1: T0 + 3600000, timeLength: 600, stopGap: 40, labelSize: 100 };

  it("puts time across and stations down, in stop order", () => {
    const l = layoutMarey(rows, { ...opts, orientation: "across" });
    expect(l.stops.map((s) => s.name)).toEqual(["A", "B"]);
    expect(l.stops[1]!.y - l.stops[0]!.y).toBe(40);
    expect(l.trips[0]!.points[0]).toMatchObject({ x: 100, method: "observed" });
    expect(l.trips[0]!.points[1]).toMatchObject({ x: 200, y: l.stops[1]!.y, method: "estimated" });
    expect(l.ticks.length).toBe(7);
  });

  it("swaps the axes for the phone layout", () => {
    const l = layoutMarey(rows, { ...opts, orientation: "down" });
    expect(l.stops[1]!.x - l.stops[0]!.x).toBe(40);
    expect(l.trips[0]!.points[1]!.y).toBeGreaterThan(l.trips[0]!.points[0]!.y);
  });
});
