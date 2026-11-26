import { Metric, Withheld } from "@/components/trust";
import { Table, Td } from "@/components/ui";
import { bandLabel, headlineBand, WINDOW_LABEL } from "@/lib/linedata";
import type { BudgetData, BudgetRow } from "@/lib/types";

const excluded = (r: BudgetRow) =>
  [
    r.n_excluded_outage ? `${r.n_excluded_outage} in our data gaps` : "",
    r.n_implausible ? `${r.n_implausible} implausible` : "",
    r.n_disruption ? `${r.n_disruption} during a service disruption (kept)` : "",
  ]
    .filter(Boolean)
    .join(", ") || undefined;

/**
 * The answer: "Usually 14 min; leave 21 min to arrive on time 95% of the time", with the trips behind
 * it, the coverage and window, and every number a popover button. A pair with no measured band (all
 * withheld) says so with the hatch instead of a number; the table below lists every band.
 */
export function BudgetResult({ line, data, now }: { line: string; data: BudgetData; now: Date }) {
  const from = data.from_station?.name ?? "";
  const to = data.to_station?.name ?? "";
  const win = WINDOW_LABEL[data.window] ?? data.window;
  const head = headlineBand(data.rows, now);
  const info = (r: BudgetRow, label: string) => ({
    k: "trip_budget",
    line,
    n: r.n_trips,
    window: data.window,
    excluded: excluded(r),
    label: `${label}, ${from} to ${to}, ${bandLabel(r.time_band)}`,
  });
  const cell = (r: BudgetRow, v: number | null) => ({ value: v, coverage_pct: r.coverage_pct, withheld_reason: r.withheld_reason });
  return (
    <div className="grid gap-4" data-testid="budget-result">
      {head ? (
        <div className="rounded-lg border border-line bg-card p-4">
          <p className="text-sm text-muted">
            {from} to {to}, {bandLabel(head.time_band)}, based on {win}
          </p>
          <p className="mt-1 flex flex-wrap items-start gap-x-2 gap-y-1 text-xl" data-testid="budget-sentence">
            <span>Usually</span>
            <Metric cell={cell(head, head.median_s)} unit="min0" size="lg" info={info(head, "Usual travel time (median)")} />
            <span>; leave</span>
            <Metric cell={cell(head, head.p95_s)} unit="min0" size="lg" info={info(head, "Time to leave (95th percentile)")} />
            <span>to arrive on time 95% of the time.</span>
          </p>
          <p className="mt-2 text-sm text-muted">
            {head.n_trips} trips measured of {head.n_scheduled_trips} scheduled ({Math.round(head.coverage_pct ?? 0)}% coverage). The slowest 1 in 20 trips took longer than the second number.
          </p>
        </div>
      ) : (
        <div className="rounded-lg border border-line bg-card p-4" data-testid="budget-none">
          <p className="font-semibold">
            No budget for {from} to {to} yet
          </p>
          <p className="mt-1 text-sm text-muted">Not enough measured trips in {win} for any time of day. The table lists each one and why.</p>
        </div>
      )}
      <Table caption={`Travel time from ${from} to ${to}, ${win}`} head={["Time of day", "Usually", "Leave", "Buffer", "Trips measured"]}>
        {data.rows.map((r) => (
          <tr key={`${r.direction_id}-${r.time_band}`} data-status={r.status}>
            <Td>{bandLabel(r.time_band)}</Td>
            {r.status === "measured" ? (
              <>
                <Td><Metric cell={cell(r, r.median_s)} unit="min0" info={info(r, "Usual travel time (median)")} /></Td>
                <Td><Metric cell={cell(r, r.p95_s)} unit="min0" info={info(r, "Time to leave (95th percentile)")} /></Td>
                <Td>{r.buffer_time_index == null ? "" : `+${Math.round(r.buffer_time_index * 100)}%`}</Td>
              </>
            ) : (
              <Td colSpan={3}>
                {r.status === "no_trips" ? (
                  <span className="text-muted" title={r.no_trips_reason ?? undefined} data-testid="no-trips">No trips measured yet</span>
                ) : (
                  <Withheld coverage={r.coverage_pct} reason={r.withheld_reason} />
                )}
              </Td>
            )}
            <Td>{r.n_trips} of {r.n_scheduled_trips}</Td>
          </tr>
        ))}
      </Table>
      <p className="text-sm text-muted">
        Buffer is how much longer the slowest 1 in 20 trips took than the usual one. Trips are real trips of the {line.replace("-", " ")} line observed at both stations; times of day are by the scheduled start. Nothing here is a promise.
      </p>
    </div>
  );
}
