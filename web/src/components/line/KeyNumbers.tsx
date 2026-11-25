import { Metric } from "@/components/trust";
import { leftOutText } from "@/lib/definitions";
import { lineName } from "@/lib/lines";
import type { Cell } from "@/lib/map";
import type { AccuracyRow, ActualRow, HeadwayRow } from "@/lib/types";

const cell = (r: { coverage_pct: number | null; withheld_reason: string | null } | undefined, v: number | null | undefined): Cell =>
  r ? { value: v ?? null, coverage_pct: r.coverage_pct, withheld_reason: r.withheld_reason } : { value: null, coverage_pct: null };

/**
 * Four key numbers, each a button. Heavy rail leads with spacing; light rail leads with on time.
 */
export function KeyNumbers({
  line,
  heavy,
  window,
  h,
  a,
  acc,
  leftOut,
}: {
  line: string;
  heavy: boolean;
  window: "last_24h" | "last_7d";
  h: HeadwayRow | undefined;
  a: ActualRow | undefined;
  acc: AccuracyRow | undefined;
  /** Headways left out because our own capture had a gap, summed over the line. Null when unknown. */
  leftOut?: number | null;
}) {
  const nm = lineName(line);
  const ex = leftOutText(leftOut ?? null, "headways");
  const hi = { line, n: h?.n_observations, window, excluded: ex };
  const tiles: { key: string; label: string; body: React.ReactNode }[] = [
    {
      key: "within",
      label: "Evenly spaced",
      body: <Metric size="lg" cell={cell(h, h?.pct_within_band)} unit="pct0" info={{ ...hi, k: "within_band", label: `${nm}: evenly spaced` }} />,
    },
    {
      key: "bunched",
      label: "Bunched · gaps",
      body: (
        <span className="flex flex-wrap items-start gap-x-4 gap-y-2">
          <span className="flex flex-col items-start" data-testid="bunched-value">
            <span className="text-xs text-muted">Bunched</span>
            <Metric size="lg" cell={cell(h, h?.pct_bunched)} unit="pct0" info={{ ...hi, k: "bunched", label: `${nm}: bunched` }} />
          </span>
          <span className="flex flex-col items-start" data-testid="gaps-value">
            <span className="text-xs text-muted">Gaps</span>
            <Metric size="lg" cell={cell(h, h?.pct_gap)} unit="pct0" info={{ ...hi, k: "gap", label: `${nm}: gaps` }} />
          </span>
        </span>
      ),
    },
    {
      key: "ontime",
      label: heavy ? "On time (secondary)" : "On time",
      body: <Metric size="lg" cell={cell(a, a?.pct_on_time_window)} unit="pct0" info={{ line, n: a?.n_observations, window, excluded: leftOutText(null), k: "on_time", label: `${nm}: on time` }} />,
    },
    {
      key: "countdown",
      label: "Countdown error, 0–5 min ahead",
      body: <Metric size="lg" cell={cell(acc, acc?.median_abs_error_s)} unit="s20" info={{ line, n: acc?.n_observations, window, k: "accuracy", label: `${nm}: median countdown error, 0-5 min ahead` }} />,
    },
  ];
  const order = heavy ? tiles : [tiles[2], tiles[0], tiles[1], tiles[3]];
  return (
    <dl className="grid grid-cols-1 gap-3 min-[480px]:grid-cols-2 lg:grid-cols-4" data-testid="key-numbers">
      {order.map((t) => (
        <div key={t!.key} className="rounded-lg border border-line bg-card p-3">
          <dt className="mb-1 text-sm text-muted">{t!.label}</dt>
          <dd>{t!.body}</dd>
        </div>
      ))}
    </dl>
  );
}
