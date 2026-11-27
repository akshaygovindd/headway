import { describe, expect, it } from "vitest";
import { hourState, orderHours, tripCell } from "@/lib/linedata";
import { cleanHeader, disruptionLabel, INFERRED_SKIP_TEXT, lineDisruptionHeader } from "@/lib/network";
import type { DisruptionRow, SegmentRow, SpacingHour, TripTimeRow } from "@/lib/types";

const trip = (o: Partial<TripTimeRow>): TripTimeRow => ({
  direction_id: 0, time_band: "midday", n_trips: 5, n_scheduled_trips: 6, n_excluded_outage: 0, n_implausible: 0, n_disruption: 0,
  median_ett_s: 60, p90_ett_s: 120, median_scheduled_s: 1800, median_actual_s: 1860, coverage_pct: 80, status: "measured",
  withheld_reason: null, no_trips_reason: null, ...o,
});
const hour = (o: Partial<SpacingHour>): SpacingHour => ({
  service_date: "2026-11-23", hour_local: 8, hour_start: "2026-11-23T13:00:00Z", n_headways: 9, pct_within_band: 85, pct_bunched: 5, pct_gap: 10,
  scheduled_headways: 10, coverage_pct: 90, data_outage: false, status: "measured", withheld_reason: null, ...o,
});
const alert = (o: Partial<DisruptionRow>): DisruptionRow => ({
  alert_id: "a", effect: "SHUTTLE", header_text: "Shuttle buses replace trains", first_seen: "", last_seen: "", is_active: true, n_stations: 2,
  from_station_name: null, to_station_name: null, segments: [], ...o,
});
const seg = (o: Partial<SegmentRow>) => ({ disrupted: false, disruption_header: null, ...o }) as SegmentRow;

describe("trip-time states", () => {
  it("separates no_trips, withheld and measured", () => {
    expect(tripCell(trip({ status: "no_trips", n_trips: 0, median_ett_s: null }))).toMatchObject({ kind: "no_trips" });
    expect(tripCell(trip({ status: "withheld", median_ett_s: null, coverage_pct: 35, withheld_reason: "low" }))).toEqual({ kind: "withheld", coverage: 35, reason: "low" });
    expect(tripCell(trip({}))).toEqual({ kind: "measured" });
  });
});

describe("spacing by hour", () => {
  it("buckets measured hours with the health ramp and keeps other states distinct", () => {
    expect(hourState(hour({ pct_within_band: 85 }))).toBe("even");
    expect(hourState(hour({ pct_within_band: 65 }))).toBe("some_bunching");
    expect(hourState(hour({ pct_within_band: 40 }))).toBe("bunched_gaps");
    expect(hourState(hour({ status: "outage", data_outage: true, pct_within_band: null }))).toBe("outage");
    expect(hourState(hour({ status: "withheld", pct_within_band: null }))).toBe("withheld");
    expect(hourState(hour({ status: "no_headways", pct_within_band: null }))).toBe("no_headways");
  });

  it("orders 05:00 to 01:00", () => {
    expect(orderHours([hour({ hour_local: 0 }), hour({ hour_local: 23 }), hour({ hour_local: 5 })]).map((h) => h.hour_local)).toEqual([5, 23, 0]);
  });
});

describe("disruption text", () => {
  it("prefers the active alert, then any alert, then the segment header", () => {
    expect(lineDisruptionHeader([alert({ is_active: false, header_text: "old" }), alert({ header_text: "now" })])).toBe("now");
    expect(lineDisruptionHeader([alert({ is_active: false, header_text: "old" })])).toBe("old");
    expect(lineDisruptionHeader(null, [seg({}), seg({ disrupted: true, disruption_header: "from segment" })])).toBe("from segment");
    expect(lineDisruptionHeader([], [seg({ disrupted: true, disruption_header: null })])).toBeNull();
    expect(lineDisruptionHeader(undefined, [seg({ disrupted: false, disruption_header: "stale" })])).toBeNull();
  });

  it("keeps header text plain and as given", () => {
    expect(cleanHeader("  <b>Shuttle</b>\n buses  ")).toBe("<b>Shuttle</b> buses");
    expect(cleanHeader("   ")).toBeNull();
  });

  it("infers a skipped stop only when there is no header text, and says so", () => {
    expect(disruptionLabel("Shuttle buses", true)).toMatchObject({ kind: "header", text: "Service disruption: Shuttle buses" });
    expect(disruptionLabel(null, true)).toMatchObject({ kind: "inferred", text: INFERRED_SKIP_TEXT });
    expect(INFERRED_SKIP_TEXT).toBe("Possible stop skipped (inferred from incidents)");
    expect(disruptionLabel(null, false)).toMatchObject({ kind: "generic", text: "Service disruption" });
  });
});

describe("countdownRowsFor", () => {
  it("drops rows naming another line, whatever kind of stop ids the segments carry", async () => {
    const { countdownRowsFor } = await import("@/lib/linedata");
    const rows = [
      { station_id: "place-mlmnl", line: "Orange" },
      { station_id: "place-davis", line: "Orange" },
      { station_id: "place-asmnl", line: "Red" },
    ];
    // /segments carries platform stop ids ("70036"), countdown rows station ids: no match is needed
    expect(countdownRowsFor(rows, "Orange", new Set(["70036", "70034"]))).toEqual([rows[0], rows[1]]);
    expect(countdownRowsFor(rows, "Orange", new Set())).toEqual([rows[0], rows[1]]);
  });

  it("uses the line's stops only for a row that names no line", async () => {
    const { countdownRowsFor } = await import("@/lib/linedata");
    const rows = [{ station_id: "place-mlmnl" }, { station_id: "place-davis" }];
    expect(countdownRowsFor(rows, "Orange", new Set(["place-mlmnl"]))).toEqual([rows[0]]);
  });
});
