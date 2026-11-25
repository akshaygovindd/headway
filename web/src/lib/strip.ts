// Where the station names go under the station strip. Pure, so collisions can be tested.
//
// Names sit at stop points (the boundaries between segments), not under segments. If every name
// fits flat on two alternating rows they stay flat; otherwise all are angled. The first and last
// station are always labelled, and anchored so they stay inside the strip.

export type StripLabel = {
  /** 0 is the first stop, `names.length - 1` the last. */
  index: number;
  name: string;
  /** Position along the strip, 0 to 100. */
  pct: number;
  row: 0 | 1;
  anchor: "start" | "middle" | "end";
};
export type StripPlan = { mode: "flat" | "angled"; labels: StripLabel[] };

const CHAR_PX = 6.2; // 11 px text
const GAP_PX = 6;

/** Stop names in order: the first segment's from-stop, then every segment's to-stop. */
export function stopNames(segs: { from_stop_name: string; to_stop_name: string }[]): string[] {
  const first = segs[0];
  return first ? [first.from_stop_name, ...segs.map((s) => s.to_stop_name)] : [];
}

type Box = { lo: number; hi: number; row: number };

export function flatBoxes(names: string[], widthPx: number): Box[] {
  const n = Math.max(1, names.length - 1);
  return names.map((name, i) => {
    const w = name.length * CHAR_PX;
    const x = (i / n) * widthPx;
    const anchor = i === 0 ? "start" : i === names.length - 1 ? "end" : "middle";
    const lo = anchor === "start" ? x : anchor === "end" ? x - w : x - w / 2;
    return { lo, hi: lo + w, row: i % 2 };
  });
}

/** True if any two flat labels on the same row would touch. */
export function collides(boxes: Box[]): boolean {
  for (const row of [0, 1]) {
    const r = boxes.filter((b) => b.row === row).sort((a, b) => a.lo - b.lo);
    for (let i = 1; i < r.length; i++) if ((r[i] as Box).lo < (r[i - 1] as Box).hi + GAP_PX) return true;
  }
  return false;
}

export function labelPlan(names: string[], widthPx = 960): StripPlan {
  const n = Math.max(1, names.length - 1);
  const flat = !collides(flatBoxes(names, widthPx));
  const labels = names.map<StripLabel>((name, i) => ({
    index: i,
    name,
    pct: (i / n) * 100,
    row: flat ? ((i % 2) as 0 | 1) : 0,
    anchor: flat ? (i === 0 ? "start" : i === names.length - 1 ? "end" : "middle") : i === names.length - 1 ? "end" : "start",
  }));
  return { mode: flat ? "flat" : "angled", labels };
}
