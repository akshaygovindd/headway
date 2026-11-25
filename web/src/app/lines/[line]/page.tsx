import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CsvButton } from "@/components/CsvButton";
import { Metric } from "@/components/trust";
import { Card, ErrorState, PageHeader, Table, Td } from "@/components/ui";
import type { ApiResult } from "@/lib/api";
import type { CsvRow, CsvSpec } from "@/lib/csv";
import { headwayCell, lineSections, onTimeCell } from "@/lib/data";
import { isHeavyRail, isLine, LINES, trend, type Cell, type Trend } from "@/lib/map";
import type { CoveredRow, Section } from "@/lib/types";

export const revalidate = 300;

export function generateStaticParams() {
  return LINES.map((line) => ({ line }));
}

export async function generateMetadata({ params }: { params: Promise<{ line: string }> }): Promise<Metadata> {
  const { line } = await params;
  return { title: isLine(line) ? `${line} line` : "Line" };
}

const cellOf = (row: CoveredRow, value: number | null): Cell => ({
  value,
  coverage_pct: row.coverage_pct,
  withheld_reason: row.withheld_reason,
});

function TrendText({ t, goodWhenUp, unit }: { t: Trend; goodWhenUp: boolean; unit: string }) {
  if (t.kind === "withheld") return <span className="text-sm italic text-muted">Trend withheld: one of the two periods has too little data</span>;
  if (t.kind === "flat") return <span>Steady: last 24 hours within {unit} of the 7-day figure</span>;
  const better = (t.kind === "up") === goodWhenUp;
  return (
    <span>
      {t.kind === "up" ? "Up" : "Down"} {Math.abs(t.delta)} {unit} against the 7-day figure ({better ? "better" : "worse"})
    </span>
  );
}

function Missing<T>({ r, what, children }: { r: ApiResult<T>; what: string; children: (b: T) => ReactNode }) {
  return r.ok ? <>{children(r.body.data)}</> : <ErrorState failure={r} what={what} />;
}

function Rows<R extends CoveredRow>({ s, children }: { s: Section<R> | undefined; children: (r: R) => ReactNode }) {
  if (!s || s.rows.length === 0) return <p className="text-sm text-muted">No rows for this period.</p>;
  return <>{s.rows.map(children)}</>;
}

/** The rows the cards show, one per displayed number. Section names match the card titles. */
function csvRows(
  line: string,
  s: Awaited<ReturnType<typeof lineSections>>,
  dayCell: Cell,
  weekCell: Cell,
): CsvRow[] {
  const out: CsvRow[] = [];
  const add = (section: string, metric: string, key: string, cell: Cell, unit: CsvRow["unit"]) =>
    out.push({ labels: { line, section, metric, key }, cell, unit });
  if (s.h24.ok) {
    for (const r of s.h24.body.data.headway_adherence?.rows ?? []) {
      add("Headway adherence", "Within the expected spacing", "", cellOf(r, r.pct_within_band), "pct");
      add("Headway adherence", "Bunched trains", "", cellOf(r, r.pct_bunched), "pct");
      add("Headway adherence", "Gaps", "", cellOf(r, r.pct_gap), "pct");
    }
  }
  if (s.rel24.ok) {
    for (const r of s.rel24.body.data.actual_deviation?.rows ?? []) {
      add("Reliability", "Arrivals on time (last 24 hours)", "", cellOf(r, r.pct_on_time_window), "pct");
      add("Reliability", "Median deviation from the timetable", "", cellOf(r, r.median_deviation_s), "s");
      add("Reliability", "90th percentile deviation", "", cellOf(r, r.p90_deviation_s), "s");
    }
    for (const r of s.rel24.body.data.predicted_deviation?.rows ?? []) {
      add("Reliability", "Median predicted deviation", "", cellOf(r, r.median_predicted_deviation_s), "s");
      add("Reliability", "Median prediction revision", "", cellOf(r, r.median_abs_revision_s), "s");
      add("Reliability", "Skipped stops", "", cellOf(r, r.skipped_share), "share");
    }
  }
  if (s.acc.ok) {
    for (const r of s.acc.body.data.prediction_accuracy?.rows ?? []) {
      add("Accuracy of MBTA predictions", "Within 60 s", r.horizon_bucket, cellOf(r, r.pct_within_60s), "pct");
      add("Accuracy of MBTA predictions", "Within 120 s", r.horizon_bucket, cellOf(r, r.pct_within_120s), "pct");
      add("Accuracy of MBTA predictions", "Median error", r.horizon_bucket, cellOf(r, r.median_abs_error_s), "s");
    }
  }
  if (s.cd.ok) {
    for (const r of (s.cd.body.data.countdown_accuracy?.rows ?? []).slice(0, 40)) {
      const key = `${r.station_name} @ ${r.countdown_min} min`;
      add("Countdown accuracy", "Within 60 s", key, cellOf(r, r.pct_within_60s), "pct");
      add("Countdown accuracy", "Within 120 s", key, cellOf(r, r.pct_within_120s), "pct");
    }
  }
  add("Trend", "Last 24 hours", "", dayCell, "pct");
  add("Trend", "Last 7 days", "", weekCell, "pct");
  return out;
}

export default async function LinePage({ params }: { params: Promise<{ line: string }> }) {
  const { line } = await params;
  if (!isLine(line)) notFound();
  const sections = await lineSections(line);
  const { h24, h7, rel24, rel7, acc, cd } = sections;
  const heavy = isHeavyRail(line);
  const fresh = [h24, rel24, acc].find((r) => r.ok);

  const headwaysCard = (
    <Card key="headways" title="Headway adherence" definition="8-headway-adherence-release-2">
      <Missing r={h24} what="Headway adherence">
        {(d) => (
          <Rows s={d.headway_adherence}>
            {(r) => (
              <dl key="h" className="grid gap-3 sm:grid-cols-3">
                <div><dt className="text-sm text-muted">Within the expected spacing</dt><dd><Metric cell={cellOf(r, r.pct_within_band)} unit="pct" /></dd></div>
                <div><dt className="text-sm text-muted">Bunched trains</dt><dd><Metric cell={cellOf(r, r.pct_bunched)} unit="pct" /></dd></div>
                <div><dt className="text-sm text-muted">Gaps</dt><dd><Metric cell={cellOf(r, r.pct_gap)} unit="pct" /></dd></div>
              </dl>
            )}
          </Rows>
        )}
      </Missing>
    </Card>
  );

  const reliabilityCard = (
    <Card key="reliability" title="Reliability" definition="7-actual-schedule-deviation-release-2">
      <Missing r={rel24} what="Reliability">
        {(d) => (
          <div className="grid gap-4 sm:grid-cols-2">
            <Rows s={d.actual_deviation}>
              {(r) => (
                <dl key="a" className="grid gap-3">
                  <div><dt className="text-sm text-muted">Arrivals on time (last 24 hours)</dt><dd><Metric cell={cellOf(r, r.pct_on_time_window)} unit="pct" /></dd></div>
                  <div><dt className="text-sm text-muted">Median deviation from the timetable</dt><dd><Metric cell={cellOf(r, r.median_deviation_s)} unit="s" /></dd></div>
                  <div><dt className="text-sm text-muted">90th percentile deviation</dt><dd><Metric cell={cellOf(r, r.p90_deviation_s)} unit="s" /></dd></div>
                </dl>
              )}
            </Rows>
            <Rows s={d.predicted_deviation}>
              {(r) => (
                <dl key="p" className="grid gap-3">
                  <div><dt className="text-sm text-muted">Median predicted deviation</dt><dd><Metric cell={cellOf(r, r.median_predicted_deviation_s)} unit="s" /></dd></div>
                  <div><dt className="text-sm text-muted">Median prediction revision</dt><dd><Metric cell={cellOf(r, r.median_abs_revision_s)} unit="s" /></dd></div>
                  <div><dt className="text-sm text-muted">Skipped stops</dt><dd><Metric cell={cellOf(r, r.skipped_share)} unit="share" /></dd></div>
                </dl>
              )}
            </Rows>
          </div>
        )}
      </Missing>
    </Card>
  );

  const accuracyCard = (
    <Card key="accuracy" title="Accuracy of MBTA predictions" definition="release-2-metrics-shared-rules">
      <Missing r={acc} what="Prediction accuracy">
        {(d) => (
          <Table caption={`Prediction accuracy by horizon, ${line} line`} head={["Minutes ahead", "Within 60 s", "Within 120 s", "Median error"]}>
            {(d.prediction_accuracy?.rows ?? []).map((r) => (
              <tr key={r.horizon_bucket}>
                <Td>{r.horizon_bucket}</Td>
                <Td><Metric cell={cellOf(r, r.pct_within_60s)} unit="pct" /></Td>
                <Td><Metric cell={cellOf(r, r.pct_within_120s)} unit="pct" /></Td>
                <Td><Metric cell={cellOf(r, r.median_abs_error_s)} unit="s" /></Td>
              </tr>
            ))}
          </Table>
        )}
      </Missing>
    </Card>
  );

  const countdownCard = (
    <Card key="countdown" title="Countdown accuracy" definition="10-countdown-accuracy-release-2">
      <Missing r={cd} what="Countdown accuracy">
        {(d) => (
          <Table caption={`Countdown accuracy by station, ${line} line`} head={["Station", "Countdown", "Within 60 s", "Within 120 s"]}>
            {(d.countdown_accuracy?.rows ?? []).slice(0, 40).map((r) => (
              <tr key={`${r.station_id}-${r.countdown_min}`}>
                <Td>{r.station_name}</Td>
                <Td>{r.countdown_min} min</Td>
                <Td><Metric cell={cellOf(r, r.pct_within_60s)} unit="pct" /></Td>
                <Td><Metric cell={cellOf(r, r.pct_within_120s)} unit="pct" /></Td>
              </tr>
            ))}
          </Table>
        )}
      </Missing>
    </Card>
  );

  const dayCell = heavy
    ? headwayCell(h24.ok ? h24.body.data.headway_adherence?.rows[0] : undefined)
    : onTimeCell(rel24.ok ? rel24.body.data.actual_deviation?.rows[0] : undefined);
  const weekCell = heavy
    ? headwayCell(h7.ok ? h7.body.data.headway_adherence?.rows[0] : undefined)
    : onTimeCell(rel7.ok ? rel7.body.data.actual_deviation?.rows[0] : undefined);

  const trendCard = (
    <Card key="trend" title="Trend: last 24 hours against 7 days">
      <div className="grid gap-3 sm:grid-cols-3">
        <div><p className="text-sm text-muted">Last 24 hours</p><Metric cell={dayCell} unit="pct" /></div>
        <div><p className="text-sm text-muted">Last 7 days</p><Metric cell={weekCell} unit="pct" /></div>
        <p aria-live="polite"><TrendText t={trend(dayCell, weekCell, 1)} goodWhenUp unit="points" /></p>
      </div>
    </Card>
  );

  const csv: CsvSpec = {
    labelColumns: ["line", "section", "metric", "key"],
    rows: csvRows(line, sections, dayCell, weekCell),
    definition: "8-headway-adherence-release-2",
    freshness: fresh?.ok ? fresh.body.freshness.exported_at : null,
  };

  return (
    <>
      <PageHeader
        title={line === "Mattapan" ? "Mattapan trolley" : `${line} line`}
        intro={heavy ? "Heavy rail runs to headways, so spacing between trains comes first." : "Light rail: on-time share against the timetable comes first."}
        freshness={fresh?.ok ? fresh.body.freshness : null}
      />
      <p className="mb-4">
        <CsvButton spec={csv} filename={`headway-${line}.csv`} />
      </p>
      <div className="grid gap-4">
        {heavy ? [headwaysCard, reliabilityCard] : [reliabilityCard, headwaysCard]}
        {accuracyCard}
        {countdownCard}
        {trendCard}
      </div>
    </>
  );
}
