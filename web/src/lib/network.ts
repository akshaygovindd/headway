// Rows for the network diagram and the station strip, from /segments. Pure.

import { segmentState, type SegmentState } from "./health";
import type { DisruptionRow, SegmentRow } from "./types";

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

/** Plain text only: collapse whitespace and control characters. React escapes it; it is never HTML. */
export function cleanHeader(s: string | null | undefined): string | null {
  if (typeof s !== "string") return null;
  const t = s.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return t || null;
}

/**
 * The alert header to show for a line: the active alert from /disruptions (it lists active first),
 * else any alert of the window, else the first disrupted segment's `disruption_header`. Null when no
 * text is exported anywhere (the endpoint is missing, or only the last_7d segments are shown).
 */
export function lineDisruptionHeader(alerts: DisruptionRow[] | null | undefined, rows: SegmentRow[] = []): string | null {
  const list = alerts ?? [];
  const pick = list.find((a) => a.is_active && cleanHeader(a.header_text)) ?? list.find((a) => cleanHeader(a.header_text));
  if (pick) return cleanHeader(pick.header_text);
  for (const r of rows) {
    if (r.disrupted) {
      const h = cleanHeader(r.disruption_header);
      if (h) return h;
    }
  }
  return null;
}

export type DisruptionLabel =
  | { kind: "header"; text: string; full: string }
  | { kind: "inferred"; text: string; full: string }
  | { kind: "generic"; text: string; full: string };

export const INFERRED_SKIP_TEXT = "Possible stop skipped (inferred from incidents)";

/**
 * What a chip says. The header text wins; with none, a "stops skipped" threshold on an open
 * incident gives an explicitly inferred label; else the generic words.
 */
export function disruptionLabel(header: string | null, skippedInferred: boolean): DisruptionLabel {
  if (header) return { kind: "header", text: `Service disruption: ${header}`, full: `Service disruption: ${header}` };
  if (skippedInferred) return { kind: "inferred", text: INFERRED_SKIP_TEXT, full: INFERRED_SKIP_TEXT };
  return { kind: "generic", text: "Service disruption", full: "Service disruption" };
}
