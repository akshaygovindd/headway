import { lineName } from "@/lib/lines";
import type { SegmentRow, SlowSegmentRow } from "@/lib/types";

const SEVERITY_H: Record<string, string> = { minor: "h-6", moderate: "h-8", severe: "h-10" };
const keyOf = (a: string | null, b: string | null) => [a, b].sort().join("|");

/**
 * One line drawn as its segments in order (direction 0, the trunk plus one branch, as on the line
 * page). A flagged segment is taller by severity and filled dark; a segment flagged but withheld for
 * coverage is hatched; the rest are thin and pale. Never colour alone: every flagged segment is also
 * described in words (screen readers and the table below). Flags in either direction mark the
 * stretch between the same two stops.
 */
export function SlowZoneLine({ line, segments, slow }: { line: string; segments: SegmentRow[]; slow: SlowSegmentRow[] }) {
  const ordered = segments
    .filter((s) => s.direction_id === 0)
    .sort((a, b) => a.segment_order - b.segment_order)
    .filter((s, i, all) => i === 0 || s.segment_order !== all[i - 1]?.segment_order);
  const worst = new Map<string, SlowSegmentRow>();
  const rank = (s: SlowSegmentRow) => (s.withheld_reason ? 0 : ({ minor: 1, moderate: 2, severe: 3 } as const)[s.severity ?? "minor"]);
  for (const s of slow) {
    const k = keyOf(s.from_stop_id, s.to_stop_id);
    const cur = worst.get(k);
    if (!cur || rank(s) > rank(cur)) worst.set(k, s);
  }
  const label = `${lineName(line)}: segments in order, slow ones marked`;
  const word = (s: SlowSegmentRow | undefined, from: string, to: string) =>
    !s ? `${from} to ${to}: not flagged` : s.withheld_reason ? `${from} to ${to}: flagged, withheld (${s.withheld_reason})` : `${from} to ${to}: ${s.severity} slow segment`;
  return (
    <div data-testid="slow-line" data-line={line}>
      <ol className="flex items-end gap-0.5" aria-label={label}>
        {ordered.map((seg) => {
          const s = worst.get(keyOf(seg.from_stop_id, seg.to_stop_id));
          const cls = !s ? "h-3 bg-line" : s.withheld_reason ? "hatch-withheld h-6" : `${SEVERITY_H[s.severity ?? "minor"]} bg-fg`;
          return (
            <li key={`${seg.from_stop_id}-${seg.to_stop_id}`} className="min-w-1.5 flex-1" data-flag={s ? (s.withheld_reason ? "withheld" : (s.severity ?? "minor")) : "none"}>
              <span aria-hidden="true" title={word(s, seg.from_stop_name, seg.to_stop_name)} className={`block w-full rounded-[2px] ${cls}`} />
              <span className="sr-only">{word(s, seg.from_stop_name, seg.to_stop_name)}</span>
            </li>
          );
        })}
      </ol>
      <p className="mt-1 flex justify-between text-xs text-muted" aria-hidden="true">
        <span>{ordered[0]?.from_stop_name}</span>
        <span>{ordered[ordered.length - 1]?.to_stop_name}</span>
      </p>
    </div>
  );
}
