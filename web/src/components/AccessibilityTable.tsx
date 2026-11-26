import { Metric, Withheld } from "@/components/trust";
import { Table, Td } from "@/components/ui";
import { lineName } from "@/lib/lines";
import type { AccessibilityStation } from "@/lib/types";

/** "4,698 h (195 days)": the age of an open outage in words a rider can read. */
export function ageText(hours: number): string {
  if (hours < 48) return `${Math.round(hours)} hours`;
  const days = hours / 24;
  if (days < 60) return `${Math.round(days)} days`;
  const months = days / 30.4;
  return months < 24 ? `${Math.round(months)} months` : `${(days / 365.25).toFixed(1)} years`;
}

/**
 * One row per station with an announced outage or one out now. "Out now" is the latest alerts
 * capture and is a word, not a colour; the window figures are withheld under 50% coverage.
 */
export function AccessibilityTable({ rows, window }: { rows: AccessibilityStation[]; window: string }) {
  return (
    <Table caption="Elevator and escalator outages announced per station" head={["Station", "Lines", "Right now", "Outage hours", "Outages", "Longest open outage"]}>
      {rows.map((s) => {
        const cell = { value: s.outage_hours, coverage_pct: s.coverage_pct, withheld_reason: s.withheld_reason };
        return (
          <tr key={s.station_id} data-testid="station-row" data-out={s.currently_out}>
            <Td><span className="font-semibold">{s.station_name}</span></Td>
            <Td>{s.lines.map(lineName).join(", ")}</Td>
            <Td>
              {s.currently_out ? (
                <span data-testid="out-now">
                  <strong>Out now</strong>
                  <ul className="mt-1 list-disc pl-4 text-sm">
                    {s.current_outage_text.map((t) => <li key={t}>{t}</li>)}
                  </ul>
                </span>
              ) : (
                <span className="text-muted">None announced now</span>
              )}
            </Td>
            <Td>
              {s.outage_hours == null ? (
                <Withheld coverage={s.coverage_pct} reason={s.withheld_reason} />
              ) : (
                <Metric cell={cell} unit="h" info={{ k: "outage_hours", window, n: s.n_outages, label: `Outage hours, ${s.station_name}` }} />
              )}
            </Td>
            <Td>
              {s.n_outages == null ? "" : `${s.n_outages}${s.n_elevator_outages || s.n_escalator_outages ? ` (${[s.n_elevator_outages ? `${s.n_elevator_outages} elevator` : "", s.n_escalator_outages ? `${s.n_escalator_outages} escalator` : ""].filter(Boolean).join(", ")})` : ""}`}
            </Td>
            <Td>{s.longest_open_outage_h != null ? ageText(s.longest_open_outage_h) : ""}</Td>
          </tr>
        );
      })}
    </Table>
  );
}
