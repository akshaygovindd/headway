import { Metric, Withheld } from "@/components/trust";
import { Table, Td } from "@/components/ui";
import { bandLabel } from "@/lib/linedata";
import { lineName } from "@/lib/lines";
import { dateTimeET } from "@/lib/time";
import type { SlowComparisonRow, SlowSegmentRow, SlowZoneStatus } from "@/lib/types";

export const STATUS_TEXT: Record<SlowZoneStatus, string> = {
  matched: "On the official list and slow here",
  observed_only: "Slow here, not on the official list",
  listed_only: "On the official list, not slow here",
  listed_unmapped: "On the official list, not matched to a segment",
};

const SEVERITY_TEXT = { minor: "Minor", moderate: "Moderate", severe: "Severe" } as const;
const stops = (r: { from_stop_name: string | null; to_stop_name: string | null }) => (r.from_stop_name && r.to_stop_name ? `${r.from_stop_name} to ${r.to_stop_name}` : "");

/** The segments flagged slow (an observed signal): by line, segment and time of day, with their numbers. */
export function ObservedTable({ rows, window }: { rows: SlowSegmentRow[]; window: string }) {
  return (
    <Table caption="Slow segments seen in the data, an observed signal not the official list" head={["Line", "Segment", "Time of day", "Slower than normal by", "Severity", "Seen", "Trips"]}>
      {rows.map((r) => {
        const cell = { value: r.excess_s, coverage_pct: r.coverage_pct, withheld_reason: r.withheld_reason };
        return (
          <tr key={`${r.line}-${r.direction_id}-${r.from_stop_id}-${r.to_stop_id}-${r.time_band}`} data-status={r.withheld_reason ? "withheld" : "flagged"}>
            <Td>{lineName(r.line)}</Td>
            <Td>{stops(r)}</Td>
            <Td>{bandLabel(r.time_band)}</Td>
            <Td>
              <Metric cell={cell} unit="s" info={{ k: "slow_segment", line: r.line, n: r.n_trips, window, label: `Slower than normal, ${stops(r)}, ${bandLabel(r.time_band)}`, excluded: r.n_disruption ? `${r.n_disruption} during a service disruption (kept)` : undefined }} />
            </Td>
            <Td>{r.severity ? SEVERITY_TEXT[r.severity] : <Withheld coverage={r.coverage_pct} reason={r.withheld_reason} />}</Td>
            <Td>{r.first_seen && r.last_seen ? `${dateTimeET(r.first_seen)} to ${dateTimeET(r.last_seen)}` : ""}</Td>
            <Td>{r.n_trips}</Td>
          </tr>
        );
      })}
    </Table>
  );
}

/** The three-way comparison with the official list, one row per segment (or unmatched restriction). */
export function ComparisonTable({ rows }: { rows: SlowComparisonRow[] }) {
  return (
    <Table caption="Observed slow segments compared with the MBTA's list of speed restrictions" head={["In the data and on the list?", "Line", "Segment", "Observed", "Official limit", "Reported"]}>
      {rows.map((r) => {
        const cell = { value: r.worst_excess_s, coverage_pct: r.coverage_pct };
        return (
          <tr key={r.restriction_ids.join(",") + (r.from_stop_id ?? "") + (r.to_stop_id ?? "") + r.line + (r.direction_id ?? "")} data-status={r.status}>
            <Td><span className="font-semibold">{STATUS_TEXT[r.status]}</span></Td>
            <Td>{lineName(r.line)}</Td>
            <Td>{stops(r) || (r.location_text ?? "")}</Td>
            <Td>
              {r.worst_excess_s != null ? (
                <span className="inline-flex flex-wrap items-start gap-x-2">
                  <Metric cell={cell} unit="s" info={{ k: "slow_segment", line: r.line, n: r.n_trips, window: "last_7d", label: `Slower than normal, ${stops(r)}` }} />
                  <span className="text-sm">{r.severity ? SEVERITY_TEXT[r.severity].toLowerCase() : ""}{r.flagged_bands.length ? `, ${r.flagged_bands.map(bandLabel).join(", ")}` : ""}</span>
                </span>
              ) : (
                <span className="text-muted">{r.observed_status === "not_enough_data" ? "Not enough data" : "Not slow"}</span>
              )}
            </Td>
            <Td>{r.official_speed_limit_mph != null ? `${r.official_speed_limit_mph} mph` : ""}</Td>
            <Td>{r.official_date_reported ?? ""}</Td>
          </tr>
        );
      })}
    </Table>
  );
}
