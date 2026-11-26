import type { Metadata } from "next";
import { AccessibilityTable } from "@/components/AccessibilityTable";
import { Card, ErrorState, PageHeader } from "@/components/ui";
import { getApi } from "@/lib/api";
import { pageMeta } from "@/lib/site";
import type { AccessibilityData } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = pageMeta(
  "Elevator and escalator outages",
  "Which subway stations have an elevator or escalator announced out of service, for how long, and the longest outage still open.",
  "/accessibility",
);

export default async function AccessibilityPage() {
  const r = await getApi<AccessibilityData>("/accessibility?window=last_24h");
  const d = r.ok ? r.body.data : null;
  const first = d?.stations[0];
  return (
    <>
      <PageHeader
        title="Elevator and escalator outages"
        intro="Stations where the MBTA has announced an elevator or escalator out of service, from its service alerts. These are announcements, not the equipment's own status: a station with nothing listed may still have a broken lift."
      />
      {!r.ok || !d ? (
        <ErrorState failure={r.ok ? { ok: false, kind: "unavailable", status: null, message: "" } : r} what="Accessibility" />
      ) : (
        <div className="grid gap-6">
          <section aria-labelledby="now-h">
            <h2 id="now-h" className="mb-2 text-xl">Right now</h2>
            <p className="rounded-lg border border-line bg-card p-4" data-testid="out-now-summary">
              <strong className="text-2xl tabular-nums">{d.n_stations_out_now}</strong> of {d.n_stations_in_network} stations have an elevator or escalator announced out of service in the latest alerts.
              {first?.withheld_reason ? <span className="block text-sm text-muted">The hours below cover the last 24 hours: some are withheld because there is too little capture coverage.</span> : null}
            </p>
          </section>
          <section aria-labelledby="table-h">
            <h2 id="table-h" className="mb-2 text-xl">By station, last 24 hours</h2>
            <Card title="Stations with an announced outage" definition="elevator-and-escalator-outages-agg_accessibility_outages">
              {d.stations.length ? (
                <AccessibilityTable rows={d.stations} window={d.window} />
              ) : (
                <p className="text-sm text-muted" data-testid="none-announced">No elevator or escalator outage is announced at any station. That is what was announced, not a check of the equipment.</p>
              )}
              <p className="mt-2 text-sm text-muted">Outage hours are the hours of the last 24 an announced outage covered the station, overlapping announcements counted once; hours when our own capture had a gap are left out. An announcement with no end time that is still showing is open; its age counts from when the MBTA says it started.</p>
            </Card>
          </section>
        </div>
      )}
    </>
  );
}
