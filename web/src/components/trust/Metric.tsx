import { formatValue, shown, type Cell, type Unit } from "@/lib/map";
import { CoverageBadge } from "./CoverageBadge";
import { Withheld } from "./Withheld";

/**
 * The only way a number reaches the page: a value renders with its coverage beside it, and a
 * withheld value renders the Withheld text and no digits of the value.
 */
export function Metric({ cell, unit }: { cell: Cell; unit: Unit }) {
  const s = shown(cell);
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-2 gap-y-1" data-testid="metric">
      {s.kind === "value" ? (
        <>
          <span className="text-lg font-semibold tabular-nums">
            {formatValue(s.value, unit)}
          </span>
          <CoverageBadge coverage={s.coverage} />
        </>
      ) : (
        <Withheld coverage={s.coverage} reason={s.reason} />
      )}
    </span>
  );
}
