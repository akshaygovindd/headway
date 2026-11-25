import { LineBadge } from "@/components/LineBadge";
import { Metric } from "@/components/trust";
import { lineName } from "@/lib/lines";
import { shown, TIMING_FLOOR_S, type Cell } from "@/lib/map";

export type AccuracyRowData = { line: string; near: Cell; far: Cell; n: number | null };

const W = 240;
const PAD = 8;

/**
 * Prediction accuracy per line: the median error of the MBTA's countdown 0-5 minutes ahead (dot)
 * and 10-20 minutes ahead (ring), joined by a connector. Withheld lines are hatched. The drawing
 * is decorative; the numbers beside it carry the values.
 */
export function AccuracyPlot({ rows }: { rows: AccuracyRowData[] }) {
  const values = rows.flatMap((r) => [shown(r.near), shown(r.far)]).flatMap((s) => (s.kind === "value" ? [s.value] : []));
  const max = Math.max(60, Math.ceil(Math.max(0, ...values) / 30) * 30);
  const x = (v: number) => PAD + (Math.min(v, max) / max) * (W - 2 * PAD);
  return (
    <div>
      <ul className="grid gap-2" aria-label="Prediction accuracy by line">
        {rows.map((r) => {
          const a = shown(r.near);
          const b = shown(r.far);
          const none = a.kind !== "value" && b.kind !== "value";
          return (
            <li key={r.line} className="grid items-center gap-x-4 gap-y-1 sm:grid-cols-[11rem_15rem_1fr]">
              <span className="flex items-center gap-2 font-semibold">
                <LineBadge line={r.line} size={24} />
                {lineName(r.line)}
              </span>
              {none ? (
                <span className="hatch-withheld h-6 rounded" data-testid="accuracy-withheld" aria-hidden="true" />
              ) : (
                <svg viewBox={`0 0 ${W} 24`} width={W} height={24} aria-hidden="true" className="max-w-full">
                  <line x1={PAD} x2={W - PAD} y1={12} y2={12} stroke="#D5D9E0" strokeWidth={2} />
                  {a.kind === "value" && b.kind === "value" ? <line x1={x(a.value)} x2={x(b.value)} y1={12} y2={12} stroke="#3346B8" strokeWidth={3} /> : null}
                  {a.kind === "value" ? <circle cx={x(a.value)} cy={12} r={6} fill="#3346B8" /> : null}
                  {b.kind === "value" ? <circle cx={x(b.value)} cy={12} r={6} fill="#fff" stroke="#3346B8" strokeWidth={3} /> : null}
                </svg>
              )}
              <span className="flex flex-wrap items-center gap-x-4 text-sm">
                <span className="flex items-center gap-1">0&ndash;5 min <Metric cell={r.near} unit="s20" info={{ k: "accuracy", line: r.line, n: r.n, window: "last_24h", label: `${lineName(r.line)}: median error, 0-5 min ahead` }} /></span>
                <span className="flex items-center gap-1">10&ndash;20 min <Metric cell={r.far} unit="s20" info={{ k: "accuracy", line: r.line, n: r.n, window: "last_24h", label: `${lineName(r.line)}: median error, 10-20 min ahead` }} /></span>
              </span>
            </li>
          );
        })}
      </ul>
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm" aria-label="Legend">
        <li className="flex items-center gap-1.5"><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="6" fill="#3346B8" /></svg> Dot: error 0&ndash;5 min ahead</li>
        <li className="flex items-center gap-1.5"><svg width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="5.5" fill="#fff" stroke="#3346B8" strokeWidth="2.5" /></svg> Ring: error 10&ndash;20 min ahead</li>
        <li className="flex items-center gap-1.5"><span aria-hidden="true" className="hatch-withheld inline-block h-3 w-5" /> Not enough data</li>
      </ul>
      <p className="mt-2 text-sm text-muted">
        Smaller is better. Our own timing is only good to about &plusmn;{TIMING_FLOOR_S} s, so smaller errors read &ldquo;~{TIMING_FLOOR_S} s&rdquo;. The axis runs from 0 to {max} s.
      </p>
    </div>
  );
}
