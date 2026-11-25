import { describe, expect, it } from "vitest";
import { capturePct, checkCounts, heartbeatBars, outageHours } from "@/lib/heartbeat";
import { hhmmET, hourET, SERVICE_HOURS } from "@/lib/time";
import type { Slo } from "@/lib/types";

const hb = (hour_start: string, missed: number, outage = false) => ({ hour_start, expected_runs: 60, missed_runs: missed, feed_gap_min: missed, data_outage: outage });
const slo = (over: Partial<Slo>): Slo => ({
  slo_name: "x", window: "last_7d", target_pct: 99, good_events: 1, total_events: 1, actual_pct: 100, met: true,
  error_budget_used: 0, days_covered: 7, counts_toward_slo: true, ...over,
});

describe("heartbeat", () => {
  it("computes capture share, outage hours and bar heights", () => {
    const rows = [hb("2026-11-24T01:00:00Z", 0), hb("2026-11-24T02:00:00Z", 30, true)];
    expect(capturePct(rows)).toBe(75);
    expect(capturePct([])).toBeNull();
    expect(outageHours(rows)).toBe(1);
    expect(heartbeatBars(rows).map((b) => [b.completeness, b.outage])).toEqual([[1, false], [0.5, true]]);
  });

  it("counts failing checks and warnings from the 7-day SLOs", () => {
    expect(checkCounts([slo({ met: false }), slo({ error_budget_used: 0.8 }), slo({ counts_toward_slo: false, met: null }), slo({}), slo({ window: "last_30d", met: false })])).toEqual({ failing: 1, warnings: 2 });
    expect(checkCounts([slo({ window: "last_30d", met: false })])).toEqual({ failing: 1, warnings: 0 });
  });
});

describe("Eastern time", () => {
  it("shows ET, not UTC, across the November clock", () => {
    expect(hhmmET("2026-11-24T11:45:00Z")).toBe("06:45");
    expect(hhmmET("2026-07-01T11:45:00Z")).toBe("07:45");
    expect(hhmmET(null)).toBeNull();
    expect(hourET("2026-11-24T02:00:00Z")).toBe(21);
  });

  it("runs the service day from 05:00 to 01:00", () => {
    expect(SERVICE_HOURS).toHaveLength(20);
    expect(SERVICE_HOURS[0]).toBe(5);
    expect(SERVICE_HOURS[19]).toBe(0);
  });
});
