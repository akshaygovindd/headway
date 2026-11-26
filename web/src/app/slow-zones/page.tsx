import type { Metadata } from "next";
import { SlowZoneLine } from "@/components/slow/SlowZoneLine";
import { ComparisonTable, ObservedTable, STATUS_TEXT } from "@/components/slow/SlowTables";
import { Card, ErrorState, PageHeader } from "@/components/ui";
import { getApi } from "@/lib/api";
import { lineName } from "@/lib/lines";
import { LINES } from "@/lib/map";
import { pageMeta } from "@/lib/site";
import { segmentsOf } from "@/lib/site-data";
import type { SlowZoneStatus, SlowZonesData } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = pageMeta(
  "Slow zones",
  "Stretches of the subway where trains are consistently slower than usual, seen in the data, compared with the MBTA's own list of speed restrictions.",
  "/slow-zones",
);

const ORDER: SlowZoneStatus[] = ["matched", "observed_only", "listed_only", "listed_unmapped"];

export default async function SlowZonesPage() {
  const r = await getApi<SlowZonesData>("/slow-zones?window=last_7d");
  const d = r.ok ? r.body.data : null;
  const flaggedLines = d ? LINES.filter((l) => d.segments.some((s) => s.line === l)) : [];
  const diagrams = await Promise.all(flaggedLines.map(async (l) => ({ line: l, seg: await segmentsOf(l, "last_24h") })));
  return (
    <>
      <PageHeader
        title="Slow zones"
        intro="Stretches where trains took consistently longer than usual, seen in Headway's data. This is an observed speed-restriction signal, not the MBTA's official list; the official list is shown next to it for comparison."
      />
      {!r.ok || !d ? (
        <ErrorState failure={r.ok ? { ok: false, kind: "unavailable", status: null, message: "" } : r} what="Slow zones" />
      ) : (
        <div className="grid gap-6">
          <Card title="Where the official list comes from" definition="release-4-site-metrics">
            <div data-testid="official-provenance" data-status={d.official.status}>
              {d.official.status === "captured" ? (
                <>
                  <p>
                    <strong>Snapshot of the MBTA&apos;s list, captured {d.official.snapshot_date}.</strong> Copied by hand from the MBTA&apos;s public speed-restrictions page; it is not a live feed and may be out of date.
                  </p>
                  {d.official.source_url ? (
                    <p className="mt-1 text-sm text-muted">
                      Source: <a href={d.official.source_url}>{d.official.source_url}</a>
                    </p>
                  ) : null}
                </>
              ) : (
                <p>
                  <strong>Official list not captured yet.</strong> Until someone copies the MBTA&apos;s list in by hand, every slow segment below is shown as slow in our data only; nothing can be said about whether it is on the official list.
                </p>
              )}
            </div>
          </Card>

          <section aria-labelledby="summary-h">
            <h2 id="summary-h" className="mb-2 text-xl">Seen in the data and on the official list</h2>
            <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Counts by status">
              {ORDER.map((k) => (
                <li key={k} className="rounded-lg border border-line bg-card p-4" data-testid={`count-${k}`}>
                  <p className="text-3xl font-extrabold tabular-nums">{d.counts[k]}</p>
                  <p className="text-sm text-muted">{STATUS_TEXT[k]}</p>
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="lines-h">
            <h2 id="lines-h" className="mb-2 text-xl">Where, line by line</h2>
            {diagrams.length ? (
              <div className="grid gap-4 rounded-lg border border-line bg-card p-4">
                {diagrams.map(({ line, seg }) => (
                  <div key={line}>
                    <h3 className="mb-1 font-semibold">{lineName(line)}</h3>
                    <SlowZoneLine line={line} segments={seg.all} slow={d.segments.filter((s) => s.line === line)} />
                  </div>
                ))}
                <p className="text-sm text-muted">Taller dark bars are slow segments (taller is slower); hatched means flagged but too little data to show a number; thin pale bars are the rest of the line. Direction 0 is drawn; the table lists both directions.</p>
              </div>
            ) : (
              <p className="rounded-lg border border-line bg-card p-4 text-sm text-muted" data-testid="no-slow">No segment is consistently slower than usual in the last 7 days, or not enough data to say.</p>
            )}
          </section>

          <section aria-labelledby="observed-h">
            <h2 id="observed-h" className="mb-2 text-xl">Slow in the data</h2>
            <div className="rounded-lg border border-line bg-card p-4">
              {d.segments.length ? (
                <ObservedTable rows={d.segments} window={d.window} />
              ) : (
                <p className="text-sm text-muted">Nothing flagged.</p>
              )}
              <p className="mt-2 text-sm text-muted">A segment is flagged when its typical time is at least 20% and 30 seconds over its own 7-day normal, on at least 15 trips, most of them slow. It can be a speed restriction, a recurring signal problem or a reconstruction error: it is a signal, not a cause.</p>
            </div>
          </section>

          <section aria-labelledby="compare-h">
            <h2 id="compare-h" className="mb-2 text-xl">Compared with the official list</h2>
            <div className="rounded-lg border border-line bg-card p-4">
              {d.comparison.length ? (
                <ComparisonTable rows={d.comparison} />
              ) : (
                <p className="text-sm text-muted" data-testid="no-comparison">
                  {d.official.status === "captured" ? "Nothing is slow in the data or on the list." : "Nothing to compare yet."}
                </p>
              )}
            </div>
          </section>
        </div>
      )}
    </>
  );
}
