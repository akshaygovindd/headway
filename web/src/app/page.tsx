import { pageMeta } from "@/lib/site";
import { AccuracyPlot, type AccuracyRowData } from "@/components/home/AccuracyPlot";
import { LineCards, type CardChips } from "@/components/home/LineCards";
import { NetworkDiagram, RampLegend } from "@/components/home/NetworkDiagram";
import { StateKey } from "@/components/home/StateKey";
import { TrustStrip } from "@/components/home/TrustStrip";
import { ErrorState } from "@/components/ui";
import { capturePct, checkCounts } from "@/lib/heartbeat";
import { heroSentence } from "@/lib/hero";
import { shown } from "@/lib/map";
import { homeData, openIncidentsByLine, thresholds, withheldWhy } from "@/lib/site-data";
import { hhmmET } from "@/lib/time";

export const revalidate = 300;
export const metadata = pageMeta(
  "Lines",
  "How reliable is the Boston subway right now? Open measurements with their coverage and freshness shown beside every number.",
  "/",
);

export default async function Home() {
  const d = await homeData();
  if (!d.lines.ok) {
    return (
      <>
        <h1 className="text-3xl">Boston subway reliability</h1>
        <div className="mt-4"><ErrorState failure={d.lines} /></div>
      </>
    );
  }
  const open = openIncidentsByLine(d.incidents);
  const asOf = d.meta.ok ? d.meta.body.freshness.metrics_as_of : d.lines.body.freshness.metrics_as_of;
  const time = hhmmET(asOf);

  const chips = (line: string, anyDisrupted: boolean): CardChips => {
    const inc = open?.get(line) ?? [];
    return { disrupted: inc.length > 0 || anyDisrupted, skipped: inc.some((i) => thresholds(i).includes("skipped")) };
  };
  const cards = d.perLine.map(({ line, stat, seg }) => ({
    stat,
    chips: chips(line, seg.segs.some((s) => s.row.disrupted)),
  }));
  const hero = heroSentence(
    d.perLine.map(({ stat }, i) => {
      const s = shown(stat.cell);
      return {
        line: stat.line,
        kind: stat.kind,
        value: s.kind === "value" ? s.value : null,
        reason: s.kind === "value" ? null : withheldWhy(stat.cell, cards[i]?.chips.disrupted ?? false),
      };
    }),
  );
  const accuracy: AccuracyRowData[] = d.perLine.map(({ line, acc }) => {
    const rows = acc.ok ? (acc.body.data.prediction_accuracy?.rows ?? []) : [];
    const cell = (bucket: string) => {
      const r = rows.find((x) => x.horizon_bucket === bucket);
      return r
        ? { value: r.median_abs_error_s, coverage_pct: r.coverage_pct, withheld_reason: r.withheld_reason }
        : { value: null, coverage_pct: null };
    };
    return { line, near: cell("0-5"), far: cell("10-20"), n: rows.find((x) => x.horizon_bucket === "0-5")?.n_observations ?? null };
  });
  const covs = d.lines.body.data.map((l) => l.coverage_pct_last_24h).filter((c): c is number => c != null);
  const measured = covs.length ? covs.reduce((a, b) => a + b, 0) / covs.length : null;

  return (
    <>
      <p className="text-sm font-semibold uppercase tracking-wide text-muted" data-testid="eyebrow">
        Boston subway · last 24 hours{time ? ` · as of ${time} ET` : ""}
      </p>
      <h1 className="mt-2 max-w-4xl text-2xl leading-snug sm:text-4xl" data-testid="hero">{hero}</h1>

      <section className="mt-8" aria-labelledby="network-h">
        <h2 id="network-h" className="text-xl">Spacing along every line</h2>
        <p className="mb-3 mt-1 text-sm text-muted">Each bar is the stretch between two stations, over the last 24 hours.</p>
        <div className="rounded-lg border border-line bg-card p-4">
          <NetworkDiagram rows={d.perLine.map(({ line, seg }) => ({ line, segs: seg.segs }))} />
          <RampLegend />
        </div>
      </section>

      <div className="mt-8">
        <TrustStrip
          asOf={asOf}
          capture={d.heartbeat.ok ? capturePct(d.heartbeat.body.data.rows) : null}
          measured={measured}
          checks={d.slo.ok ? checkCounts(d.slo.body.data.slos) : null}
          heartbeat={d.heartbeat.ok ? d.heartbeat.body.data.rows : null}
        />
      </div>

      <section className="mt-8" aria-labelledby="cards-h">
        <h2 id="cards-h" className="text-xl">Line by line</h2>
        <p className="mb-3 mt-1 text-sm text-muted">Always in the same order. Press any number to see how it was measured.</p>
        <LineCards cards={cards} />
      </section>

      <section className="mt-8" id="accuracy" aria-labelledby="accuracy-h">
        <h2 id="accuracy-h" className="text-xl">How accurate are the MBTA&rsquo;s predictions?</h2>
        <p className="mb-3 mt-1 text-sm text-muted">Median gap between the countdown and the train that arrived, last 24 hours.</p>
        <div className="rounded-lg border border-line bg-card p-4"><AccuracyPlot rows={accuracy} /></div>
      </section>

      <section className="mt-8" aria-labelledby="key-h">
        <h2 id="key-h" className="text-xl">How to read Headway</h2>
        <div className="mt-3 rounded-lg border border-line bg-card p-4"><StateKey /></div>
      </section>
    </>
  );
}
