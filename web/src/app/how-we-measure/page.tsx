import type { Metadata } from "next";
import { pageMeta } from "@/lib/site";
import { Card, ErrorState, PageHeader } from "@/components/ui";
import { getApi } from "@/lib/api";
import { methodologyUrl } from "@/lib/map";
import type { MetricDef } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = pageMeta("How we measure", "Definitions of every Headway metric, the minimum-coverage rule and how freshness is reported.", "/how-we-measure");

export default async function HowWeMeasure() {
  const r = await getApi<MetricDef[]>("/metrics");
  return (
    <>
      <PageHeader
        title="How we measure"
        intro="Each figure on this site has a written definition. Headway approximates MBTA service from public feeds; it is not an official MBTA measure."
      />
      <div className="grid gap-4">
        <Card title="The coverage rule">
          <p>
            {r.ok
              ? r.body.coverage.rule
              : "A value from a row whose coverage is under 50% is withheld: shown as not enough data, with the coverage next to it."}
          </p>
          <p className="mt-2 text-sm text-muted">
            Coverage is the share of scheduled trips Headway could observe in the period.
          </p>
        </Card>
        {!r.ok ? (
          <ErrorState failure={r} what="Metric definitions" />
        ) : (
          <Card title="Metric definitions">
            <ul className="grid gap-4">
              {r.body.data.map((m) => (
                <li key={m.name}>
                  <h3 className="font-semibold">{m.label}</h3>
                  <p className="text-sm text-muted">
                    {m.higher_is_worse ? "Higher is worse" : "Higher is better"}. Windows: {m.windows.join(", ").replaceAll("_", " ")}.
                  </p>
                  <a className="text-sm" href={methodologyUrl(m.metrics_md)}>
                    Definition in METRICS.md
                  </a>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </>
  );
}
