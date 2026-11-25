// Rows for the network diagram and the station strip, from /segments. Pure.

import { segmentState, type SegmentState } from "./health";
import type { SegmentRow } from "./types";

export type Seg = { row: SegmentRow; state: SegmentState };

/**
 * One path through a line in one direction: ordered by segment_order. Branches share positions
 * (Red, Green), so a position keeps the first segment seen: the diagram shows the trunk plus one
 * branch, and the line page lists the stop names it drew.
 */
export function orderedSegments(rows: SegmentRow[], direction = 0): Seg[] {
  const seen = new Set<number>();
  return rows
    .filter((r) => r.direction_id === direction)
    .sort((a, b) => a.segment_order - b.segment_order)
    .filter((r) => (seen.has(r.segment_order) ? false : (seen.add(r.segment_order), true)))
    .map((row) => ({ row, state: segmentState(row) }));
}

export type Stretch = { from: string; to: string; start: number; end: number };

/** Runs of consecutive disrupted segments, as index ranges into the ordered list. */
export function disruptedStretches(segs: Seg[]): Stretch[] {
  const out: Stretch[] = [];
  let open: Stretch | null = null;
  segs.forEach((s, i) => {
    if (s.row.disrupted) {
      if (open) {
        open.end = i;
        open.to = s.row.to_stop_name;
      } else {
        open = { from: s.row.from_stop_name, to: s.row.to_stop_name, start: i, end: i };
        out.push(open);
      }
    } else {
      open = null;
    }
  });
  return out;
}

export function stateCounts(segs: Seg[]): Record<SegmentState, number> {
  const c: Record<SegmentState, number> = { even: 0, some_bunching: 0, bunched_gaps: 0, withheld: 0 };
  for (const s of segs) c[s.state] += 1;
  return c;
}

/** The first and last station of the drawn path, for "Alewife to Ashmont". */
export function termini(segs: Seg[]): [string, string] | null {
  const a = segs[0];
  const b = segs[segs.length - 1];
  return a && b ? [a.row.from_stop_name, b.row.to_stop_name] : null;
}
