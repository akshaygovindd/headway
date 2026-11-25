import { heartbeatBars } from "@/lib/heartbeat";
import { hourET, hourLabel, SERVICE_HOURS } from "@/lib/time";
import type { HeartbeatRow } from "@/lib/types";

/**
 * The service day (05:00 to 01:00 ET) by hour: which hours Headway could measure and which were
 * our own data gap (hatched, not evaluated). Hourly spacing values are not in the public API yet.
 */
export function HourStrip({ rows }: { rows: HeartbeatRow[] }) {
  const byHour = new Map(heartbeatBars(rows).map((b) => [hourET(b.hour_start), b]));
  return (
    <div>
      <ol className="grid grid-cols-5 gap-1 sm:grid-cols-10 lg:grid-cols-[repeat(20,minmax(0,1fr))]" data-testid="hour-strip" aria-label="Service day by hour">
        {SERVICE_HOURS.map((h) => {
          const b = byHour.get(h);
          const state = !b ? "none" : b.outage ? "outage" : "ok";
          return (
            <li
              key={h}
              data-state={state}
              className={`rounded px-1 py-2 text-center text-xs ${state === "outage" ? "hatch-outage text-[#1F2630]" : state === "ok" ? "border border-line bg-card" : "border border-dashed border-line text-muted"}`}
            >
              <span className="block font-semibold">{hourLabel(h)}</span>
              <span className="block">{state === "outage" ? "our data gap" : state === "ok" ? "measured" : "not yet"}</span>
            </li>
          );
        })}
      </ol>
      <p className="mt-2 text-sm text-muted">
        Hatched hours are our data gap: capture was down, so the hour is left out of every number. Hourly spacing figures are not served by the API yet.
      </p>
    </div>
  );
}
