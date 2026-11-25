import type { Metadata } from "next";
import { pageMeta } from "@/lib/site";
import { Card, ErrorState, PageHeader, Table, Td } from "@/components/ui";
import { getApi } from "@/lib/api";
import { sloTitle } from "@/lib/definitions";
import { formatNumber } from "@/lib/map";
import { OwnMetric } from "@/components/trust";
import { capturePct } from "@/lib/heartbeat";
import type { HealthData, HeartbeatData, SloData } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = pageMeta("Pipeline health and SLOs", "How the Headway data pipeline is doing: runs, data quality and service-level objectives.", "/pipeline");

export default async function PipelinePage() {
  const [health, slo, heartbeat] = await Promise.all([
    getApi<HealthData>("/pipeline/health?days=7"),
    getApi<SloData>("/slo"),
    getApi<HeartbeatData>("/pipeline/heartbeat?hours=24"),
  ]);
  // The same figure, from the same function, as the "Captures" number on the home page.
  const capture = heartbeat.ok ? capturePct(heartbeat.body.data.rows) : null;
  return (
    <>
      <PageHeader
        title="Pipeline health and SLOs"
        intro="How Headway's own data collection is doing. These figures count Headway's runs and events, not train observations, so each shows the days or events it is based on instead of a coverage badge."
      />
      <div className="grid gap-4">
        <Card title="Capture, last 24 hours" definition="release-2-slos">
          {capture == null ? (
            <p data-testid="capture-24h">Data unavailable right now</p>
          ) : (
            <p data-testid="capture-24h">
              <OwnMetric value={capture} unit="pct" size="lg" info={{ k: "capture", window: "last_24h" }} />{" "}
              <span className="text-sm text-muted">of one-minute captures ran. The same figure as &ldquo;Captures&rdquo; on the home page.</span>
            </p>
          )}
        </Card>
        <Card title="Service level objectives" definition="release-2-slos">
          {!slo.ok ? (
            <ErrorState failure={slo} what="SLOs" />
          ) : (
            <Table caption="Service level objectives" head={["Objective", "Window", "Target", "Actual", "Based on", "Status"]}>
              {slo.body.data.slos.map((s) => (
                <tr key={`${s.slo_name}-${s.window}`}>
                  <Td>{sloTitle(s.slo_name)}</Td>
                  <Td>{s.window.replace("last_", "last ")}</Td>
                  <Td>{formatNumber(s.target_pct, 1)}%</Td>
                  <Td>{s.actual_pct == null ? "no data" : `${formatNumber(s.actual_pct, 2)}%`}</Td>
                  <Td>{formatNumber(s.good_events)} of {formatNumber(s.total_events)} events over {s.days_covered} {s.days_covered === 1 ? "day" : "days"}</Td>
                  <Td>{!s.counts_toward_slo ? "Too few days to count" : s.met ? "Met" : "Not met"}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
        <Card title="Daily pipeline runs" definition="4-freshness">
          {!health.ok ? (
            <ErrorState failure={health} what="Pipeline health" />
          ) : (
            <Table caption="Pipeline runs per day" head={["Date", "Runs", "Vehicle polls failed", "Trip update failures", "Duplicate deliveries", "Quarantined records"]}>
              {health.body.data.daily.map((d) => (
                <tr key={d.date}>
                  <Td>{d.date}</Td>
                  <Td>{d.runs}</Td>
                  <Td>{d.vehicle_polls_error} of {d.vehicle_polls_ok + d.vehicle_polls_error}</Td>
                  <Td>{d.trip_update_failures} of {d.runs}</Td>
                  <Td>{d.duplicate_deliveries}</Td>
                  <Td>{formatNumber(d.quarantined_records)}</Td>
                </tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </>
  );
}
