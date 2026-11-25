// The Marey chart: pure geometry and annotations, so the SVG component only draws.
// Time runs across (desktop) or down (mobile); stations run the other way.

import { hhmmET } from "./time";
import type { MareyRow } from "./types";

export type MareyAnnotation = {
  kind: "bunching" | "long_wait";
  stop_id: string;
  stop_name: string;
  /** ISO time of the later of the two trains. */
  at: string;
  gap_s: number;
  /** Median gap at this stop in the hour: what "normal" means for the comparison. */
  expected_s: number;
};

const BUNCH_BELOW = 0.5;
const LONG_ABOVE = 1.5;

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? (s[m] as number) : ((s[m - 1] as number) + (s[m] as number)) / 2;
}

/**
 * Trains that arrived much closer than usual (under half the median gap at that stop) and gaps
 * much longer than usual (over 1.5 times). Needs three trains at a stop to know "usual". Keeps
 * the worst `perKind` of each kind, one per stop, so the chart stays readable.
 */
export function annotateMarey(rows: MareyRow[], perKind = 2): MareyAnnotation[] {
  const byStop = new Map<string, MareyRow[]>();
  for (const r of rows) byStop.set(r.stop_id, [...(byStop.get(r.stop_id) ?? []), r]);
  const found: (MareyAnnotation & { severity: number })[] = [];
  for (const stopRows of byStop.values()) {
    const times = stopRows.map((r) => Date.parse(r.arrival_ts)).filter(Number.isFinite).sort((a, b) => a - b);
    if (times.length < 3) continue;
    const gaps = times.slice(1).map((t, i) => (t - (times[i] as number)) / 1000);
    const expected = median(gaps);
    if (expected <= 0) continue;
    const name = (stopRows[0] as MareyRow).stop_name;
    const stopId = (stopRows[0] as MareyRow).stop_id;
    let bunch: (typeof found)[number] | null = null;
    let long: (typeof found)[number] | null = null;
    gaps.forEach((g, i) => {
      const at = new Date(times[i + 1] as number).toISOString().replace(".000Z", "Z");
      if (g < expected * BUNCH_BELOW) {
        const severity = expected / Math.max(g, 1);
        if (!bunch || severity > bunch.severity) bunch = { kind: "bunching", stop_id: stopId, stop_name: name, at, gap_s: g, expected_s: expected, severity };
      } else if (g > expected * LONG_ABOVE) {
        const severity = g / expected;
        if (!long || severity > long.severity) long = { kind: "long_wait", stop_id: stopId, stop_name: name, at, gap_s: g, expected_s: expected, severity };
      }
    });
    if (bunch) found.push(bunch);
    if (long) found.push(long);
  }
  const top = (k: MareyAnnotation["kind"]) =>
    found.filter((a) => a.kind === k).sort((a, b) => b.severity - a.severity).slice(0, perKind);
  return [...top("bunching"), ...top("long_wait")].map((a) => ({ kind: a.kind, stop_id: a.stop_id, stop_name: a.stop_name, at: a.at, gap_s: a.gap_s, expected_s: a.expected_s }));
}

export type MareyPoint = { x: number; y: number; method: "observed" | "estimated" };
export type MareyLayout = {
  orientation: "across" | "down";
  width: number;
  height: number;
  stops: { id: string; name: string; x: number; y: number }[];
  trips: { id: string; points: MareyPoint[]; timetable: { x: number; y: number }[] }[];
  ticks: { x: number; y: number; label: string }[];
  plot: { x: number; y: number; w: number; h: number };
  position: (iso: string, stopId: string) => { x: number; y: number } | null;
};

export type LayoutOptions = {
  orientation: "across" | "down";
  t0: number;
  t1: number;
  /** Length of the time axis in px. */
  timeLength: number;
  /** Spacing between stations in px. */
  stopGap: number;
  /** Room for station names. */
  labelSize: number;
};

export function layoutMarey(rows: MareyRow[], o: LayoutOptions): MareyLayout {
  const first = new Map<string, { name: string; seq: number }>();
  for (const r of rows) {
    const cur = first.get(r.stop_id);
    if (!cur || r.stop_sequence < cur.seq) first.set(r.stop_id, { name: r.stop_name, seq: r.stop_sequence });
  }
  const ordered = [...first].sort((a, b) => a[1].seq - b[1].seq || a[1].name.localeCompare(b[1].name));
  const across = o.orientation === "across";
  const pad = 16;
  const plot = across
    ? { x: o.labelSize, y: pad + 14, w: o.timeLength, h: Math.max(1, ordered.length - 1) * o.stopGap }
    : { x: pad + 8, y: o.labelSize, w: Math.max(1, ordered.length - 1) * o.stopGap, h: o.timeLength };
  const idx = new Map(ordered.map(([id], i) => [id, i]));
  const tpos = (ms: number) => ((ms - o.t0) / (o.t1 - o.t0)) * o.timeLength;
  const at = (ms: number, i: number) =>
    across ? { x: plot.x + tpos(ms), y: plot.y + i * o.stopGap } : { x: plot.x + i * o.stopGap, y: plot.y + tpos(ms) };

  const stops = ordered.map(([id, v], i) => ({ id, name: v.name, ...at(o.t0, i) }));
  const byTrip = new Map<string, MareyRow[]>();
  for (const r of rows) byTrip.set(r.trip_id, [...(byTrip.get(r.trip_id) ?? []), r]);
  const trips = [...byTrip].map(([id, rs]) => {
    const sorted = [...rs].sort((a, b) => a.stop_sequence - b.stop_sequence);
    return {
      id,
      points: sorted.map((r) => ({ ...at(Date.parse(r.arrival_ts), idx.get(r.stop_id) as number), method: r.method })),
      timetable: sorted.filter((r) => r.scheduled_ts).map((r) => at(Date.parse(r.scheduled_ts as string), idx.get(r.stop_id) as number)),
    };
  });
  const ticks: MareyLayout["ticks"] = [];
  for (let t = Math.ceil(o.t0 / 600000) * 600000; t <= o.t1; t += 600000) {
    const p = at(t, 0);
    ticks.push({ x: p.x, y: p.y, label: hhmmET(new Date(t).toISOString()) ?? "" });
  }
  return {
    orientation: o.orientation,
    width: across ? plot.x + plot.w + pad : plot.x + plot.w + pad,
    height: across ? plot.y + plot.h + pad : plot.y + plot.h + pad,
    stops,
    trips,
    ticks,
    plot,
    position: (iso, stopId) => {
      const i = idx.get(stopId);
      return i == null ? null : at(Date.parse(iso), i);
    },
  };
}
