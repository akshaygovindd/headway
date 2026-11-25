import Link from "next/link";
import { CoverageBadge, DisruptionChip, Freshness, Metric } from "@/components/trust";
import { PageHeader } from "@/components/ui";
import { getApi } from "@/lib/api";
import { headline, openIncidentLines } from "@/lib/data";
import { LINES } from "@/lib/map";
import type { LineSummary } from "@/lib/types";
import { ErrorState } from "@/components/ui";

export const revalidate = 300;

export default async function Home() {
  const lines = await getApi<LineSummary[]>("/lines");
  if (!lines.ok) {
    return (
      <>
        <PageHeader title="Boston subway reliability" />
        <ErrorState failure={lines} />
      </>
    );
  }
  const [open, cards] = await Promise.all([
    openIncidentLines(),
    Promise.all(LINES.map((l) => headline(l))),
  ]);
  const byLine = new Map(lines.body.data.map((l) => [l.line, l]));

  return (
    <>
      <PageHeader
        title="Boston subway reliability"
        intro="Every number is shown with the share of scheduled trips we could observe. Under 50% we say so instead of guessing."
        freshness={lines.body.freshness}
      />
      <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" aria-label="Lines">
        {LINES.map((line, i) => {
          const card = cards[i];
          const summary = byLine.get(line);
          return (
            <li key={line} className="rounded-lg border border-line bg-card p-4">
              <h2 className="text-lg font-semibold">
                <Link href={`/lines/${line}`}>{line === "Mattapan" ? "Mattapan trolley" : `${line} line`}</Link>
              </h2>
              <p className="mt-1 text-sm text-muted">{card?.label}</p>
              <p className="mt-2 min-h-8">
                {card?.ok ? (
                  <Metric cell={card.cell} unit="pct" />
                ) : summary ? (
                  <CoverageBadge coverage={summary.coverage_pct_last_24h} />
                ) : (
                  <span className="text-sm italic text-muted">Not enough data: coverage unknown</span>
                )}
              </p>
              <p className="mt-2 flex flex-wrap items-center gap-2">
                <DisruptionChip open={open ? open.has(line) : null} />
              </p>
              <p className="mt-2">
                {card?.ok ? (
                  <Freshness ageMinutes={card.freshness.age_minutes} exportedAt={card.freshness.exported_at} />
                ) : (
                  <Freshness ageMinutes={lines.body.freshness.age_minutes} exportedAt={lines.body.freshness.exported_at} />
                )}
              </p>
            </li>
          );
        })}
      </ul>
    </>
  );
}
