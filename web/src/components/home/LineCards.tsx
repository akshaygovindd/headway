import Link from "next/link";
import { LineBadge } from "@/components/LineBadge";
import { DisruptionChip, Metric, StopsSkippedChip } from "@/components/trust";
import { lineName } from "@/lib/lines";
import { disruptionLabel } from "@/lib/network";
import type { LineStat } from "@/lib/site-data";

/** `header` is the alert text when the API has it; `skipped` is the inferred fallback and only set without one. */
export type CardChips = { disrupted: boolean; header: string | null; skipped: boolean };

/** Fixed order, never sorted by value. Departure-board rows on a phone, cards from `sm` up. */
export function LineCards({ cards }: { cards: { stat: LineStat; chips: CardChips }[] }) {
  return (
    <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Lines">
      {cards.map(({ stat, chips }) => {
        return (
          <li key={stat.line} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line bg-card p-3 sm:block sm:p-4">
            <div className="flex min-w-0 flex-1 items-center gap-2 sm:mb-2">
              <LineBadge line={stat.line} size={32} />
              <div>
                <h3 className="text-base leading-tight">
                  <Link href={`/lines/${stat.line}`} className="line-link">{lineName(stat.line)}</Link>
                </h3>
                <p className="text-xs text-muted">{stat.label}</p>
              </div>
            </div>
            <div className="ml-auto min-w-0 sm:ml-0">
              <Metric
                cell={stat.cell}
                unit="pct0"
                size="lg"
                block
                info={{ k: stat.k, line: stat.line, n: stat.n, window: "last_24h", label: `${lineName(stat.line)}: ${stat.label.toLowerCase()}` }}
              />
            </div>
            <div className="flex w-full min-w-0 flex-wrap items-center gap-1.5 empty:hidden sm:mt-2">
              {chips.disrupted ? <DisruptionChip open label={disruptionLabel(chips.header, false).text} /> : null}
              {chips.skipped ? <StopsSkippedChip /> : null}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
