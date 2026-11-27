import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BudgetForm } from "@/components/line/BudgetForm";
import { BudgetResult } from "@/components/line/BudgetResult";
import { Card, ErrorState } from "@/components/ui";
import { getApi } from "@/lib/api";
import { lineName } from "@/lib/lines";
import { isLine } from "@/lib/map";
import { pageMeta } from "@/lib/site";
import type { BudgetData } from "@/lib/types";

// Read per request: the answer depends on the two stations in the query string.
export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ line: string }> }): Promise<Metadata> {
  const { line } = await params;
  if (!isLine(line)) return { title: "Trip-time budget" };
  return {
    ...pageMeta(`How much time to budget on the ${lineName(line)}`, "The usual travel time between two stations and how long to leave to arrive on time 95% of the time.", `/lines/${line}/budget`),
    robots: { index: false },
  };
}

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export default async function BudgetPage({
  params,
  searchParams,
}: {
  params: Promise<{ line: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { line } = await params;
  if (!isLine(line)) notFound();
  const q = await searchParams;
  const from = one(q.from);
  const to = one(q.to);
  const window = one(q.window) === "last_24h" ? "last_24h" : "last_7d";
  const stations = await getApi<BudgetData>(`/lines/${line}/trip-time-budget`);
  const pair = from && to ? await getApi<BudgetData>(`/lines/${line}/trip-time-budget?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}&window=${window}`) : null;
  return (
    <div className="grid gap-6">
      <div>
        <p className="text-sm"><a href={`/lines/${line}#budget-h`}>&larr; {lineName(line)}</a></p>
        <h1 className="mt-1 text-3xl">How much time should I budget?</h1>
        <p className="mt-1 text-muted">Pick two stations on the {lineName(line)}.</p>
      </div>
      <Card title="Your trip" definition="trip-time-budget-agg_trip_time_budget">
        {stations.ok ? (
          <BudgetForm line={line} stations={stations.body.data.stations ?? []} from={from} to={to} window={window} />
        ) : (
          <ErrorState failure={stations} what="Trip-time budget" />
        )}
      </Card>
      {pair ? (
        pair.ok ? (
          <BudgetResult line={line} data={pair.body.data} now={new Date()} />
        ) : pair.kind === "invalid" ? (
          <div role="status" className="rounded-lg border border-line bg-card p-4" data-testid="budget-invalid">
            <p className="font-semibold">No trip runs between those two stations in that order</p>
            <p className="mt-1 text-sm text-muted">Choose two different stations that one train serves, for example not two ends of different branches. Swapping them gives the other direction.</p>
          </div>
        ) : (
          <ErrorState failure={pair} what="Trip-time budget" />
        )
      ) : null}
    </div>
  );
}
