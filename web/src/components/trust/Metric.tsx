import { formatValue, shown, type Cell, type Unit } from "@/lib/map";
import { CoverageBadge } from "./CoverageBadge";
import { Withheld } from "./Withheld";

/** What the "How this was measured" popover needs beyond the cell itself. */
export type MetricInfo = {
  /** Key into lib/definitions.ts. */
  k?: string;
  line?: string;
  /** Observations behind the number. */
  n?: number | null;
  window?: string;
  /** Plain words: what was left out of this number. */
  excluded?: string;
  /** Overrides the definition's label, e.g. "Within 60 s at 0-5 min". */
  label?: string;
};

/**
 * The only way a number reaches the page: a value renders as a button that opens the "How this
 * was measured" popover, with its coverage glyph under it; a withheld value renders the hatched
 * Withheld text and no digits of the value. The popover host (MetricPopover) listens for clicks.
 */
export function Metric({
  cell,
  unit,
  info,
  size = "md",
}: {
  cell: Cell;
  unit: Unit;
  info?: MetricInfo;
  size?: "md" | "lg";
}) {
  const s = shown(cell);
  if (s.kind !== "value") {
    return (
      <span className="inline-flex" data-testid="metric">
        <Withheld coverage={s.coverage} reason={s.reason} />
      </span>
    );
  }
  return (
    <span className="inline-flex flex-col items-start gap-0.5" data-testid="metric">
      <button
        type="button"
        className={`metric-btn tabular-nums ${size === "lg" ? "text-3xl font-extrabold" : "text-lg font-semibold"}`}
        data-k={info?.k ?? "generic"}
        data-line={info?.line}
        data-n={info?.n ?? undefined}
        data-win={info?.window}
        data-ex={info?.excluded}
        data-cov={s.coverage}
        data-label={info?.label}
        data-value={formatValue(s.value, unit)}
        aria-haspopup="dialog"
      >
        {formatValue(s.value, unit)}
      </button>
      <CoverageBadge coverage={s.coverage} />
    </span>
  );
}
