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

export type PathStop = { id: string; name: string };
export type Branch = { label: string | null; stops: PathStop[]; exclusive: Set<string> };

/**
 * The stop paths of one direction from the segment graph: one path for a plain line, one per
 * branch (trunk plus the branch) where it forks, as on Red at JFK/UMass. A branch is labelled with
 * its last stop. `exclusive` is the stops only that branch serves.
 */
export function branchPaths(segs: { direction_id: number; from_stop_id: string; from_stop_name: string; to_stop_id: string; to_stop_name: string; segment_order: number }[], direction = 0): Branch[] {
  const rows = segs.filter((r) => r.direction_id === direction).sort((a, b) => a.segment_order - b.segment_order);
  const names = new Map<string, string>();
  const next = new Map<string, string[]>();
  const hasIn = new Set<string>();
  for (const r of rows) {
    names.set(r.from_stop_id, r.from_stop_name);
    names.set(r.to_stop_id, r.to_stop_name);
    const out = next.get(r.from_stop_id) ?? [];
    if (!out.includes(r.to_stop_id)) out.push(r.to_stop_id);
    next.set(r.from_stop_id, out);
    hasIn.add(r.to_stop_id);
  }
  const start = [...next.keys()].find((id) => !hasIn.has(id));
  if (!start) return [];
  const paths: string[][] = [];
  const walk = (trail: string[], id: string, seen: Set<string>) => {
    const t = [...trail, id];
    const outs = (next.get(id) ?? []).filter((n) => !seen.has(n));
    if (!outs.length) paths.push(t);
    for (const n of outs) walk(t, n, new Set([...seen, n]));
  };
  walk([], start, new Set([start]));
  const count = new Map<string, number>();
  for (const p of paths) for (const id of p) count.set(id, (count.get(id) ?? 0) + 1);
  return paths.map((p) => ({
    label: paths.length > 1 ? (names.get(p[p.length - 1] as string) ?? null) : null,
    stops: p.map((id) => ({ id, name: names.get(id) as string })),
    exclusive: new Set(p.filter((id) => count.get(id) === 1)),
  }));
}

/** Rows for one branch: its stops only, and only trains that belong to it (trunk-only trains show on all). */
export function rowsForBranch(rows: MareyRow[], b: Branch, all: Branch[]): MareyRow[] {
  const mine = new Set(b.stops.map((x) => x.id));
  const others = new Set(all.filter((o) => o !== b).flatMap((o) => [...o.exclusive]));
  const bad = new Set(rows.filter((r) => others.has(r.stop_id)).map((r) => r.trip_id));
  return rows.filter((r) => mine.has(r.stop_id) && !bad.has(r.trip_id));
}

/** Stop ids inside a line-scope disruption, from alert segments and segments flagged `disrupted`. */
export function coveredStops(
  alerts: { is_active: boolean; segments: { direction_id: number; from_stop_id: string; to_stop_id: string }[] }[] | null | undefined,
  segs: { direction_id: number; disrupted: boolean; from_stop_id: string; to_stop_id: string }[],
  direction = 0,
): Set<string> {
  const out = new Set<string>();
  for (const a of alerts ?? []) {
    if (!a.is_active) continue;
    for (const g of a.segments) if (g.direction_id === direction) (out.add(g.from_stop_id), out.add(g.to_stop_id));
  }
  for (const g of segs) if (g.disrupted && g.direction_id === direction) (out.add(g.from_stop_id), out.add(g.to_stop_id));
  return out;
}

export type MareyPoint = { x: number; y: number; method: "observed" | "estimated" };
export type MareyLayout = {
  orientation: "across" | "down";
  width: number;
  height: number;
  stops: { id: string; name: string; x: number; y: number }[];
  /** `runs` are the drawable pieces of a train: broken wherever the train crossed a shuttle stretch. */
  trips: { id: string; points: MareyPoint[]; runs: MareyPoint[][]; timetable: { x: number; y: number }[] }[];
  /** The shuttle stretch on the station axis (px), or null. */
  band: { from: number; to: number; names: [string, string] } | null;
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
  /** The stops of the path in order (from /segments). Rows at other stops are dropped. */
  path?: PathStop[];
  /** Stops covered by a disruption: no train is drawn through them. */
  covered?: Set<string>;
};

export function layoutMarey(allRows: MareyRow[], o: LayoutOptions): MareyLayout {
  const covered = o.covered ?? new Set<string>();
  const onPath = o.path ? new Set(o.path.map((p) => p.id)) : null;
  // A path that matches few of the rows is probably another id scheme: fall back to the rows.
  const usePath = onPath != null && allRows.filter((r) => onPath.has(r.stop_id)).length >= allRows.length / 2;
  const rows = usePath ? allRows.filter((r) => onPath!.has(r.stop_id) && !covered.has(r.stop_id)) : allRows.filter((r) => !covered.has(r.stop_id));
  const first = new Map<string, { name: string; seq: number }>();
  if (usePath) o.path!.forEach((p, i) => first.set(p.id, { name: p.name, seq: i }));
  else for (const r of allRows) {
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
    const sorted = [...rs].filter((r) => idx.has(r.stop_id)).sort((a, b) => (idx.get(a.stop_id) as number) - (idx.get(b.stop_id) as number));
    const coveredIdx = ordered.map(([sid], i) => (covered.has(sid) ? i : -1)).filter((i) => i >= 0);
    const runs: MareyPoint[][] = [];
    let prev = -1;
    for (const r of sorted) {
      const i = idx.get(r.stop_id) as number;
      const pt = { ...at(Date.parse(r.arrival_ts), i), method: r.method };
      const crossed = prev >= 0 && coveredIdx.some((c) => c > prev && c < i);
      if (!runs.length || crossed) runs.push([pt]);
      else (runs[runs.length - 1] as MareyPoint[]).push(pt);
      prev = i;
    }
    return {
      id,
      runs,
      points: runs.flat(),
      timetable: sorted.filter((r) => r.scheduled_ts).map((r) => at(Date.parse(r.scheduled_ts as string), idx.get(r.stop_id) as number)),
    };
  });
  const cIdx = ordered.map(([sid], i) => (covered.has(sid) ? i : -1)).filter((i) => i >= 0);
  const axis = (i: number) => (across ? plot.y + i * o.stopGap : plot.x + i * o.stopGap);
  const band: MareyLayout["band"] = cIdx.length
    ? { from: axis(Math.min(...cIdx)), to: axis(Math.max(...cIdx)), names: [ordered[Math.min(...cIdx)]![1].name, ordered[Math.max(...cIdx)]![1].name] }
    : null;
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
    band,
    ticks,
    plot,
    position: (iso, stopId) => {
      const i = idx.get(stopId);
      return i == null ? null : at(Date.parse(iso), i);
    },
  };
}
