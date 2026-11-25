import type { Metadata } from "next";
import { pageMeta } from "@/lib/site";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { CsvButton } from "@/components/CsvButton";
import { LineBadge } from "@/components/LineBadge";
import { HourStrip } from "@/components/line/HourStrip";
import { TripTimeTable } from "@/components/line/TripTimeTable";
import { KeyNumbers } from "@/components/line/KeyNumbers";
import { MareyChart } from "@/components/line/MareyChart";
import { StationStrip } from "@/components/line/StationStrip";
import { RampLegend } from "@/components/home/NetworkDiagram";
import { DisruptionChip, Metric } from "@/components/trust";
import { Card, ErrorState, Table, Td } from "@/components/ui";
import type { ApiResult } from "@/lib/api";
import type { CsvRow, CsvSpec } from "@/lib/csv";
import { headwayCell, lineSections, onTimeCell } from "@/lib/data";
import { getApi } from "@/lib/api";
import { docUrl } from "@/lib/definitions";
import { lineName, LINE_STYLE } from "@/lib/lines";
import { isHeavyRail, isLine, LINES, shown, trend, type Cell, type Trend } from "@/lib/map";
import { disruptionLabel, lineDisruptionHeader, termini } from "@/lib/network";
import { branchPaths, coveredStops, rowsForBranch } from "@/lib/marey";
import { bandLabel, countdownRowsFor, sortBuckets } from "@/lib/linedata";
import { openIncidentsByLine, segmentsOf, thresholds } from "@/lib/site-data";
import { hhmmET } from "@/lib/time";
import notes from "@/data/metric-notes.json";
import type {
  AccuracyData,
  ActualRow,
  HeadwayRow,
  CoveredRow,
  HealthData,
  DisruptionsData,
  SpacingData,
  TripTimeData,
  IncidentsData,
  MareyData,
  MetaData,
  Section,
} from "@/lib/types";

export const revalidate = 300;

/** Day and week figures closer than this many points read as "steady". */
const TREND_FLAT_BELOW = 1;
// Only the eight known lines exist: anything else is a real 404 without calling the API.
export const dynamicParams = false;

export function generateStaticParams() {
  return LINES.map((line) => ({ line }));
}

export async function generateMetadata({ params }: { params: Promise<{ line: string }> }): Promise<Metadata> {
  const { line } = await params;
  if (!isLine(line)) return { title: "Line" };
  const name = line === "Mattapan" ? "Mattapan trolley" : `${line} line`;
  return pageMeta(name, `How reliable the ${name} is right now: headway adherence, on-time share and prediction accuracy, each with its coverage.`, `/lines/${line}`);
}

const cellOf = (row: CoveredRow, value: number | null): Cell => ({
  value,
  coverage_pct: row.coverage_pct,
  withheld_reason: row.withheld_reason,
});

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

function TrendText({ t, goodWhenUp, unit, flatBelow }: { t: Trend; goodWhenUp: boolean; unit: [string, string]; flatBelow: number }) {
  if (t.kind === "unavailable") return <span className="text-sm italic text-muted">Data unavailable right now</span>;
  if (t.kind === "withheld") return <span className="text-sm italic text-muted">Trend withheld: one of the two periods has too little data</span>;
  if (t.kind === "flat") return <span>Steady: last 24 hours within {plural(flatBelow, unit[0], unit[1])} of the 7-day figure</span>;
  const better = (t.kind === "up") === goodWhenUp;
  return (
    <span>
      {t.kind === "up" ? "Up" : "Down"} {plural(Math.abs(t.delta), unit[0], unit[1])} against the 7-day figure ({better ? "better" : "worse"})
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
  stopIds: Set<string>,
  dayCell: Cell,
  weekCell: Cell,
  segments: CsvRow[],
): CsvRow[] {
  const out: CsvRow[] = [...segments];
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
    for (const r of sortBuckets(s.acc.body.data.prediction_accuracy?.rows ?? [])) {
      add("Accuracy of MBTA predictions", "Within 60 s", r.horizon_bucket, cellOf(r, r.pct_within_60s), "pct");
      add("Accuracy of MBTA predictions", "Within 120 s", r.horizon_bucket, cellOf(r, r.pct_within_120s), "pct");
      add("Accuracy of MBTA predictions", "Median error", r.horizon_bucket, cellOf(r, r.median_abs_error_s), "s");
    }
  }
  if (s.cd.ok) {
    for (const r of countdownRowsFor(s.cd.body.data.countdown_accuracy?.rows ?? [], line, stopIds).slice(0, 40)) {
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
  const heavy = isHeavyRail(line);
  const [sections, seg24, seg7, marey, spacing, trip24, trip7, disruptions, incidents, acc7, health, meta] = await Promise.all([
    lineSections(line),
    segmentsOf(line, "last_24h"),
    segmentsOf(line, "last_7d"),
    getApi<MareyData>(`/lines/${line}/marey?direction=0`),
    getApi<SpacingData>(`/lines/${line}/spacing-by-hour`),
    getApi<TripTimeData>(`/lines/${line}/trip-time?window=last_24h`),
    getApi<TripTimeData>(`/lines/${line}/trip-time?window=last_7d`),
    getApi<DisruptionsData>(`/lines/${line}/disruptions`),
    getApi<IncidentsData>("/incidents"),
    getApi<AccuracyData>(`/lines/${line}/accuracy?window=last_7d`),
    getApi<HealthData>("/pipeline/health?days=7"),
    getApi<MetaData>("/meta"),
  ]);
  const { h24, h7, rel24, rel7, acc, cd } = sections;
  const fresh = [h24, rel24, acc].find((r) => r.ok);
  const style = LINE_STYLE[line];
  const ends = termini(seg24.segs);
  const asOf = meta.ok ? hhmmET(meta.body.freshness.metrics_as_of) : null;
  const daysAvailable = health.ok ? health.body.data.daily.length : null;
  const open = openIncidentsByLine(incidents)?.get(line) ?? [];
  const header = lineDisruptionHeader(disruptions.ok ? disruptions.body.data.rows : null, seg24.all);
  const disrupted = open.length > 0 || header != null || seg24.segs.some((x) => x.row.disrupted);
  const chipLabel = disruptionLabel(header, header == null && open.some((i) => thresholds(i).includes("skipped")));
  const stopIds = new Set(seg24.all.flatMap((r) => [r.from_stop_id, r.to_stop_id]));
  const leftOutOf = (s: typeof seg24) => (s.result.ok ? s.all.reduce((n, r) => n + (r.n_excluded_outage ?? 0), 0) : null);
  const note = (notes as Record<string, { text: string; href: string }>)[line];

  const accRow = (r: typeof acc) => (r.ok ? r.body.data.prediction_accuracy?.rows.find((x) => x.horizon_bucket === "0-5") : undefined);
  const hRow = (r: typeof h24): HeadwayRow | undefined => (r.ok ? r.body.data.headway_adherence?.rows[0] : undefined);
  const aRow = (r: typeof rel24): ActualRow | undefined => (r.ok ? r.body.data.actual_deviation?.rows[0] : undefined);

  const reliabilityCard = (
    <Card key="reliability" title="Reliability" definition="7-actual-schedule-deviation-release-2">
      <Missing r={rel24} what="Reliability">
        {(d) => (
          <div className="grid gap-4 sm:grid-cols-2">
            <Rows s={d.actual_deviation}>
              {(r) => (
                <dl key="a" className="grid gap-3">
                  <div><dt className="text-sm text-muted">Arrivals on time (last 24 hours)</dt><dd><Metric cell={cellOf(r, r.pct_on_time_window)} unit="pct" info={{ k: "on_time", line, n: r.n_observations, window: "last_24h" }} /></dd></div>
                  <div><dt className="text-sm text-muted">Median deviation from the timetable</dt><dd><Metric cell={cellOf(r, r.median_deviation_s)} unit="s" info={{ k: "deviation", line, n: r.n_observations, window: "last_24h" }} /></dd></div>
                  <div><dt className="text-sm text-muted">90th percentile deviation</dt><dd><Metric cell={cellOf(r, r.p90_deviation_s)} unit="s" info={{ k: "deviation", line, n: r.n_observations, window: "last_24h", label: "90th percentile deviation" }} /></dd></div>
                </dl>
              )}
            </Rows>
            <Rows s={d.predicted_deviation}>
              {(r) => (
                <dl key="p" className="grid gap-3">
                  <div><dt className="text-sm text-muted">Median predicted deviation</dt><dd><Metric cell={cellOf(r, r.median_predicted_deviation_s)} unit="s" info={{ k: "predicted", line, n: r.n_observations, window: "last_24h" }} /></dd></div>
                  <div><dt className="text-sm text-muted">Median prediction revision</dt><dd><Metric cell={cellOf(r, r.median_abs_revision_s)} unit="s" info={{ k: "predicted", line, n: r.n_observations, window: "last_24h", label: "Prediction revision" }} /></dd></div>
                  <div><dt className="text-sm text-muted">Skipped stops</dt><dd><Metric cell={cellOf(r, r.skipped_share)} unit="share" info={{ k: "predicted", line, n: r.n_observations, window: "last_24h", label: "Skipped stops" }} /></dd></div>
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
          <Table caption={`Prediction accuracy by horizon, ${lineName(line)}`} head={["Minutes ahead", "Within 60 s", "Within 120 s", "Median error"]}>
            {sortBuckets(d.prediction_accuracy?.rows ?? []).map((r) => (
              <tr key={r.horizon_bucket}>
                <Td>{r.horizon_bucket}</Td>
                <Td><Metric cell={cellOf(r, r.pct_within_60s)} unit="pct" info={{ k: "accuracy", line, n: r.n_observations, window: "last_24h", label: `Within 60 s, ${r.horizon_bucket} min ahead` }} /></Td>
                <Td><Metric cell={cellOf(r, r.pct_within_120s)} unit="pct" info={{ k: "accuracy", line, n: r.n_observations, window: "last_24h", label: `Within 120 s, ${r.horizon_bucket} min ahead` }} /></Td>
                <Td><Metric cell={cellOf(r, r.median_abs_error_s)} unit="s20" info={{ k: "accuracy", line, n: r.n_observations, window: "last_24h", label: `Median error, ${r.horizon_bucket} min ahead` }} /></Td>
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
          <Table caption={`Countdown accuracy by station, ${lineName(line)}`} head={["Station", "Countdown", "Within 60 s", "Within 120 s"]}>
            {countdownRowsFor(d.countdown_accuracy?.rows ?? [], line, stopIds).slice(0, 40).map((r) => (
              <tr key={`${r.station_id}-${r.countdown_min}`}>
                <Td>{r.station_name}</Td>
                <Td>{r.countdown_min} min</Td>
                <Td><Metric cell={cellOf(r, r.pct_within_60s)} unit="pct" info={{ k: "countdown", line, n: r.n_observations, window: "last_24h", label: `Within 60 s at ${r.station_name}` }} /></Td>
                <Td><Metric cell={cellOf(r, r.pct_within_120s)} unit="pct" info={{ k: "countdown", line, n: r.n_observations, window: "last_24h", label: `Within 120 s at ${r.station_name}` }} /></Td>
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
        <div><p className="text-sm text-muted">Last 24 hours</p><Metric cell={dayCell} unit="pct" info={{ k: heavy ? "within_band" : "on_time", line, window: "last_24h" }} /></div>
        <div><p className="text-sm text-muted">Last 7 days</p><Metric cell={weekCell} unit="pct" info={{ k: heavy ? "within_band" : "on_time", line, window: "last_7d" }} /></div>
        <p aria-live="polite"><TrendText t={trend(dayCell, weekCell, TREND_FLAT_BELOW)} goodWhenUp unit={["point", "points"]} flatBelow={TREND_FLAT_BELOW} /></p>
      </div>
    </Card>
  );

  const segCsv: CsvRow[] = [
    ...seg24.segs.map((x) => ({ w: "last 24 hours", x })),
    ...seg7.segs.map((x) => ({ w: "last 7 days", x })),
  ].map(({ w, x }) => ({
    labels: { line, section: "Segment health", metric: `Within the band, ${w}`, key: `${x.row.from_stop_name} to ${x.row.to_stop_name}` },
    cell: { value: x.row.pct_within_band, coverage_pct: x.row.coverage_pct, withheld_reason: x.row.withheld_reason },
    unit: "pct" as const,
  }));
  const tripCsv: CsvRow[] = [
    ...(trip24.ok ? trip24.body.data.rows.map((r) => ({ w: "last 24 hours", r })) : []),
    ...(trip7.ok ? trip7.body.data.rows.map((r) => ({ w: "last 7 days", r })) : []),
  ]
    .filter(({ r }) => r.status !== "no_trips")
    .flatMap(({ w, r }) => {
      const key = `Toward ${ends?.[r.direction_id === 0 ? 1 : 0] ?? "the end of the line"}, ${bandLabel(r.time_band)}`;
      const cell = (v: number | null) => ({ value: v, coverage_pct: r.coverage_pct, withheld_reason: r.withheld_reason });
      return ([["Median vs plan", r.median_ett_s], ["90th percentile vs plan", r.p90_ett_s], ["Planned run", r.median_scheduled_s], ["Actual run", r.median_actual_s]] as const).map(
        ([m, v]) => ({ labels: { line, section: "Trip time against the plan", metric: `${m}, ${w}`, key }, cell: cell(v), unit: "s" as const }),
      );
    });
  const csv: CsvSpec = {
    labelColumns: ["line", "section", "metric", "key"],
    rows: csvRows(line, sections, stopIds, dayCell, weekCell, [...segCsv, ...tripCsv]),
    definition: "8-headway-adherence-release-2",
    freshness: fresh?.ok ? fresh.body.freshness.exported_at : null,
  };

  const mareyAll = marey.ok ? marey.body.data.rows.filter((r) => r.direction_id === 0) : [];
  const branches = branchPaths(seg24.all, 0);
  const covered = coveredStops(disruptions.ok ? disruptions.body.data.rows : null, seg24.all, 0);
  // A withheld line (under 50% coverage) shows its chart as observations only.
  const scored = shown(dayCell).kind !== "withheld";
  const mareyViews = (branches.length > 1 ? branches : [null]).map((b) => {
    const rows = b ? rowsForBranch(mareyAll, b, branches) : mareyAll;
    const ordered = [...new Map(rows.map((r) => [r.stop_id, r])).values()].sort((x, y) => x.stop_sequence - y.stop_sequence);
    const last = b ? b.stops[b.stops.length - 1]?.name : ordered[ordered.length - 1]?.stop_name;
    return { label: b?.label ?? null, rows, path: b?.stops ?? (branches[0]?.stops), toward: last ?? ends?.[1] ?? "the end of the line" };
  });
  const hourStart = marey.ok ? marey.body.data.hour_start : null;
  const hourText = hourStart ? `${hhmmET(hourStart)}\u2013${hhmmET(new Date(Date.parse(hourStart) + 3600000).toISOString())} ET` : "";

  const strips = (label: string, segs: typeof seg24) => (
    <StationStrip segs={segs.segs} label={`${lineName(line)} stations, ${label}, coloured by spacing`} />
  );

  return (
    <div className="win-root">
      <header className="rounded-t-lg border border-b-0 border-line bg-card p-4 sm:p-5" style={{ borderBottom: `6px solid ${style?.fill ?? "#10141A"}` }} data-testid="banner">
        <div className="flex flex-wrap items-center gap-3">
          <LineBadge line={line} size={44} />
          <div>
            <h1 className="text-2xl sm:text-3xl">{lineName(line)}</h1>
            <p className="text-sm text-muted">
              {ends ? `${ends[0]} to ${ends[1]}` : heavy ? "Heavy rail" : "Light rail"}
              {asOf ? ` · as of ${asOf} ET` : ""}
            </p>
          </div>
          <fieldset className="ml-auto flex gap-1" data-testid="window-toggle">
            <legend className="sr-only">Time window</legend>
            <input type="radio" name="win" id="win-24" defaultChecked className="peer/a sr-only" />
            <label htmlFor="win-24" className="inline-flex min-h-11 cursor-pointer items-center rounded-l border border-fg px-4 text-sm font-semibold peer-checked/a:bg-fg peer-checked/a:text-white peer-focus-visible/a:outline peer-focus-visible/a:outline-[3px] peer-focus-visible/a:outline-offset-2">24 h</label>
            <input type="radio" name="win" id="win-7" className="peer/b sr-only" />
            <label htmlFor="win-7" className="inline-flex min-h-11 cursor-pointer items-center rounded-r border border-fg px-4 text-sm font-semibold peer-checked/b:bg-fg peer-checked/b:text-white peer-focus-visible/b:outline peer-focus-visible/b:outline-[3px] peer-focus-visible/b:outline-offset-2">
              7 d{daysAvailable != null ? ` (${daysAvailable} ${daysAvailable === 1 ? "day" : "days"} available)` : ""}
            </label>
          </fieldset>
        </div>
        <p className="mt-3 flex flex-wrap items-center gap-3">
          {disrupted ? <DisruptionChip open label={chipLabel.text} /> : null}
          <CsvButton spec={csv} filename={`headway-${line}.csv`} />
        </p>
      </header>

      <div className="mt-6 grid gap-6">
        <section aria-labelledby="key-h">
          <h2 id="key-h" className="mb-2 text-xl">Key numbers</h2>
          <p className="mb-2 text-sm text-muted">{heavy ? "Heavy rail runs to headways, so spacing between trains comes first." : "Light rail: on-time share against the timetable comes first."} Press a number to see how it was measured.</p>
          <div className="win-24"><KeyNumbers line={line} heavy={heavy} window="last_24h" h={hRow(h24)} a={aRow(rel24)} acc={accRow(acc)} leftOut={leftOutOf(seg24)} /></div>
          <div className="win-7"><KeyNumbers line={line} heavy={heavy} window="last_7d" h={hRow(h7)} a={aRow(rel7)} acc={accRow(acc7)} leftOut={leftOutOf(seg7)} /></div>
          {note ? (
            <p className="mt-3 rounded border border-line bg-card p-3 text-sm" data-testid="line-note">
              {note.text} <a href={docUrl(note.href)}>See the measurement</a>
            </p>
          ) : null}
        </section>

        <section aria-labelledby="strip-h">
          <h2 id="strip-h" className="mb-2 text-xl">Station by station</h2>
          <div className="rounded-lg border border-line bg-card p-4">
            <div className="win-24">{strips("last 24 hours", seg24)}</div>
            <div className="win-7">{strips("last 7 days", seg7)}</div>
            <RampLegend />
          </div>
        </section>

        <section aria-labelledby="marey-h">
          <h2 id="marey-h" className="mb-2 text-xl">Trains over the last hour</h2>
          <div className="rounded-lg border border-line bg-card p-4">
            {marey.ok && mareyAll.length && hourStart ? (
              <>
                {mareyViews.map((v) => (
                  <div key={v.label ?? "all"} className="mb-6 last:mb-0" data-testid="marey-branch">
                    <p className="mb-3 text-sm text-muted">
                      {v.label ? <strong className="text-fg">{v.label} branch. </strong> : null}
                      Trains toward {v.toward}, {hourText}. Each line is one train; steeper is faster.
                    </p>
                    <MareyChart rows={v.rows} hourStart={hourStart} toward={v.toward} path={v.path} covered={covered} scored={scored} />
                  </div>
                ))}
              </>
            ) : marey.ok ? (
              <p className="text-sm text-muted">No arrivals were reconstructed for the last complete hour.</p>
            ) : (
              <ErrorState failure={marey} what="The last-hour chart" />
            )}
          </div>
        </section>

        <section aria-labelledby="hours-h">
          <h2 id="hours-h" className="mb-2 text-xl">Spacing by hour</h2>
          <div className="rounded-lg border border-line bg-card p-4">
            {spacing.ok ? (
              spacing.body.data.hours.length ? (
                <>
                  <p className="mb-3 text-sm text-muted">Service day {spacing.body.data.service_date}, 05:00 to 01:00 Eastern, both directions combined.</p>
                  <HourStrip hours={spacing.body.data.hours} date={spacing.body.data.service_date} />
                </>
              ) : (
                <p className="text-sm text-muted">No hourly spacing rows for this service day.</p>
              )
            ) : (
              <ErrorState failure={spacing} what="Spacing by hour" />
            )}
          </div>
        </section>

        <section aria-labelledby="trip-h">
          <h2 id="trip-h" className="mb-2 text-xl">Trip time against the plan</h2>
          <div className="rounded-lg border border-line bg-card p-4">
            {[
              ["win-24", "last_24h", "last 24 hours", trip24],
              ["win-7", "last_7d", "last 7 days", trip7],
            ].map(([cls, win, label, r]) => {
              const res = r as typeof trip24;
              return (
                <div key={win as string} className={cls as string}>
                  {res.ok ? (
                    res.body.data.rows.length ? (
                      <TripTimeTable line={line} rows={res.body.data.rows} window={win as string} towards={[ends?.[0] ?? "the start of the line", ends?.[1] ?? "the end of the line"]} caption={`Trip time against the timetable, ${lineName(line)}, ${label as string}`} />
                    ) : (
                      <p className="text-sm text-muted">No scheduled trips in this window.</p>
                    )
                  ) : (
                    <ErrorState failure={res} what="Trip time" />
                  )}
                </div>
              );
            })}
            <p className="mt-2 text-sm text-muted">Median and 90th percentile are actual running time minus the planned running time of the same trips (positive is slower). An adapted approximation, not an official measure.</p>
          </div>
        </section>

        <section aria-labelledby="more-h">
          <h2 id="more-h" className="mb-2 text-xl">All measurements</h2>
          <div className="grid gap-4">
            {[reliabilityCard, accuracyCard, countdownCard, trendCard]}
          </div>
        </section>
      </div>
    </div>
  );
}
