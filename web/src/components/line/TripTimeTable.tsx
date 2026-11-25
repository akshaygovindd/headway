import { Metric, Withheld } from "@/components/trust";
import { Table, Td } from "@/components/ui";
import { bandLabel, tripCell } from "@/lib/linedata";
import type { TripTimeRow } from "@/lib/types";

/**
 * Trip time against the timetable per direction and time band. "No trips measured yet" is only for
 * a band with no measured trips; a band with trips but too little coverage is the withheld hatch.
 */
export function TripTimeTable({ line, rows, caption, towards, window }: { line: string; rows: TripTimeRow[]; caption: string; towards: [string, string]; window: string }) {
  const dirName = (d: number) => `Toward ${towards[d === 0 ? 1 : 0]}`;
  const info = (r: TripTimeRow, label: string) => ({
    k: "trip_time",
    line,
    n: r.n_trips,
    window,
    label: `${label}, ${dirName(r.direction_id)}, ${bandLabel(r.time_band)}`,
    excluded: [
      r.n_excluded_outage ? `${r.n_excluded_outage} in our data gaps` : "",
      r.n_implausible ? `${r.n_implausible} implausible` : "",
      r.n_disruption ? `${r.n_disruption} during a service disruption` : "",
    ].filter(Boolean).join(", ") || undefined,
  });
  return (
    <Table caption={caption} head={["Direction", "Time band", "Median vs plan", "90th percentile", "Planned run", "Actual run", "Trips measured"]}>
      {rows.map((r) => {
        const c = tripCell(r);
        const cell = (v: number | null) => ({ value: v, coverage_pct: r.coverage_pct, withheld_reason: r.withheld_reason });
        return (
          <tr key={`${r.direction_id}-${r.time_band}`} data-status={r.status}>
            <Td>{dirName(r.direction_id)}</Td>
            <Td>{bandLabel(r.time_band)}</Td>
            {c.kind === "measured" ? (
              <>
                <Td><Metric cell={cell(r.median_ett_s)} unit="s" info={info(r, "Median trip time vs plan")} /></Td>
                <Td><Metric cell={cell(r.p90_ett_s)} unit="s" info={info(r, "90th percentile trip time vs plan")} /></Td>
                <Td><Metric cell={cell(r.median_scheduled_s)} unit="min" info={info(r, "Planned running time")} /></Td>
                <Td><Metric cell={cell(r.median_actual_s)} unit="min" info={info(r, "Actual running time")} /></Td>
              </>
            ) : (
              <Td colSpan={4}>
                {c.kind === "no_trips" ? (
                  <span className="text-muted" title={c.reason ?? undefined} data-testid="no-trips">No trips measured yet</span>
                ) : (
                  <Withheld coverage={c.coverage} reason={c.reason} />
                )}
              </Td>
            )}
            <Td>{r.n_trips} of {r.n_scheduled_trips}</Td>
          </tr>
        );
      })}
    </Table>
  );
}
