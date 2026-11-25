import type { Metadata } from "next";
import { pageMeta } from "@/lib/site";
import { CsvButton } from "@/components/CsvButton";
import { DisruptionChip, Metric, OutageLabel, Withheld } from "@/components/trust";
import { ErrorState, LineLink, PageHeader } from "@/components/ui";
import { realIncidents } from "@/lib/site-data";
import { getApi } from "@/lib/api";
import type { CsvSpec } from "@/lib/csv";
import { shown } from "@/lib/map";
import { dateTimeET } from "@/lib/time";
import type { IncidentsData } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = pageMeta("Incidents", "Stretches of at least two hours where an MBTA subway line was clearly off, with coverage and data-outage hours marked.", "/incidents");

const when = (ts: string | null) => (ts ? dateTimeET(ts) : "ongoing");

/**
 * The incident's coverage is the API's `coverage_pct` (the mean over its evaluated hours): the same
 * hourly capture measure the line pages use, not the single worst hour.
 */
const incidentCell = (i: IncidentsData["incidents"][number]) => ({ value: i.n_hours, coverage_pct: i.coverage_pct });

function csvFor(incidents: IncidentsData["incidents"], freshness: string | null): CsvSpec {
  return {
    labelColumns: ["incident_id", "line", "start", "end", "is_open", "n_outage_hours_inside"],
    rows: incidents.map((i) => ({
      labels: {
        incident_id: i.incident_id,
        line: i.line,
        start: i.start_ts,
        end: i.end_ts,
        is_open: i.is_open,
        n_outage_hours_inside: i.n_outage_hours_inside,
      },
      // The value shown on the page is the duration, withheld by the same minimum-coverage rule.
      cell: incidentCell(i),
      unit: "h" as const,
    })),
    definition: "incidents-release-3",
    freshness,
  };
}

export default async function IncidentsPage() {
  const r = await getApi<IncidentsData>("/incidents");
  const list = realIncidents(r);
  return (
    <>
      <PageHeader
        title="Incidents"
        intro="Stretches of at least two hours where a line was clearly off. An incident is a rule result, not a verdict. Hours when Headway itself had no data are marked and never counted against service."
      />
      {!r.ok ? (
        <ErrorState failure={r} what="Incidents" />
      ) : list.length === 0 ? (
        <p>No incidents between {r.body.data.from} and {r.body.data.to}.</p>
      ) : (
        <>
        <p className="mb-4">
          <CsvButton spec={csvFor(list, r.body.freshness.exported_at)} filename="headway-incidents.csv" />
        </p>
        <ul className="grid gap-4">
          {list.map((i) => {
            const summaryOk = shown({ value: 1, coverage_pct: i.coverage_pct }).kind === "value";
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
                  Lasted <Metric cell={incidentCell(i)} unit="h" />
                </p>
                <div className="mt-2">
                  {summaryOk && i.summary_text ? <p>{i.summary_text}</p> : <Withheld coverage={i.coverage_pct} />}
                </div>
              </li>
            );
          })}
        </ul>
        </>
      )}
    </>
  );
}
