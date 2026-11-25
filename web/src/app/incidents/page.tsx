import type { Metadata } from "next";
import { DisruptionChip, Metric, OutageLabel, Withheld } from "@/components/trust";
import { ErrorState, LineLink, PageHeader } from "@/components/ui";
import { getApi } from "@/lib/api";
import { shown } from "@/lib/map";
import type { IncidentsData } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = { title: "Incidents" };

const when = (ts: string | null) =>
  ts ? new Date(ts).toLocaleString("en-US", { timeZone: "America/New_York", dateStyle: "medium", timeStyle: "short" }) : "ongoing";

export default async function IncidentsPage() {
  const r = await getApi<IncidentsData>("/incidents");
  return (
    <>
      <PageHeader
        title="Incidents"
        intro="Stretches of at least two hours where a line was clearly off. An incident is a rule result, not a verdict. Hours when Headway itself had no data are marked and never counted against service."
        freshness={r.ok ? r.body.freshness : null}
      />
      {!r.ok ? (
        <ErrorState failure={r} what="Incidents" />
      ) : r.body.data.incidents.length === 0 ? (
        <p>No incidents between {r.body.data.from} and {r.body.data.to}.</p>
      ) : (
        <ul className="grid gap-4">
          {r.body.data.incidents.map((i) => {
            const summaryOk = shown({ value: 1, coverage_pct: i.min_coverage_pct }).kind === "value";
            return (
              <li key={i.incident_id} className="rounded-lg border border-line bg-card p-4">
                <h2 className="flex flex-wrap items-center gap-2 text-lg font-semibold">
                  <LineLink line={i.line} />
                  <span className="text-sm font-normal text-muted">
                    {when(i.start_ts)} to {when(i.end_ts)}
                  </span>
                  {i.is_open ? <DisruptionChip open label="Open" /> : null}
                  <OutageLabel hours={i.n_outage_hours_inside} />
                </h2>
                <p className="mt-2 text-sm">
                  Lasted <Metric cell={{ value: i.n_hours, coverage_pct: i.min_coverage_pct }} unit="h" />
                </p>
                <div className="mt-2">
                  {summaryOk && i.summary_text ? <p>{i.summary_text}</p> : <Withheld coverage={i.min_coverage_pct} />}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
