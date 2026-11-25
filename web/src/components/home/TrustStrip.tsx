import Link from "next/link";
import { OwnMetric } from "@/components/trust";
import type { ChecksSummary } from "@/lib/checks";
import { heartbeatBars, type HeartbeatBar } from "@/lib/heartbeat";
import { hhmmET, hourLabel, hourET } from "@/lib/time";

function Bars({ bars }: { bars: HeartbeatBar[] }) {
  return (
    <div className="flex h-12 items-end gap-0.5" role="img" aria-label={`Capture heartbeat for the last ${bars.length} hours. ${bars.filter((b) => b.outage).length} hours were our data gap.`} data-testid="heartbeat">
      {bars.map((b) => (
        <span
          key={b.hour_start}
          data-outage={b.outage}
          title={`${hourLabel(hourET(b.hour_start))} ET: ${b.outage ? "our data gap" : `${Math.round(b.completeness * 100)}% of captures`}`}
          className={`block flex-1 rounded-[1px] ${b.outage ? "hatch-outage" : ""}`}
          style={b.outage ? { height: "100%" } : { height: `${Math.max(8, b.completeness * 100)}%`, background: "#8FA3FF" }}
        />
      ))}
    </div>
  );
}

/** The dark strip: how much to trust the page, from Headway's own capture. */
export function TrustStrip({
  asOf,
  capture,
  measured,
  checks,
  heartbeat,
}: {
  asOf: string | null;
  capture: number | null;
  measured: number | null;
  checks: ChecksSummary | null;
  heartbeat: Parameters<typeof heartbeatBars>[0] | null;
}) {
  const time = hhmmET(asOf);
  return (
    <section className="trust-strip rounded-lg p-5" aria-labelledby="trust-h">
      <h2 id="trust-h" className="text-lg">Can you trust this page?</h2>
      <dl className="mt-3 grid gap-x-8 gap-y-3 sm:grid-cols-4">
        <div>
          <dt className="text-xs uppercase tracking-wide text-[#aab3c2]">As of</dt>
          <dd className="text-lg font-semibold">{time ? `${time} ET` : "unknown"}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-[#aab3c2]">Captures</dt>
          <dd>{capture == null ? "n/a" : <OwnMetric value={capture} unit="pct" info={{ k: "capture", window: "last_24h" }} />}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-[#aab3c2]">Arrivals measured</dt>
          <dd>{measured == null ? "n/a" : <OwnMetric value={measured} unit="pct" info={{ k: "measured", window: "last_24h" }} />}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-[#aab3c2]">Checks</dt>
          <dd className="flex flex-wrap items-baseline gap-x-1" data-testid="checks">
            {checks ? (
              <>
                <OwnMetric value={checks.failing} unit="count" info={{ k: "checks", window: `last_${checks.hours}h`, label: "Checks failing" }} /> failing ·{" "}
                <OwnMetric value={checks.warnings} unit="count" info={{ k: "checks", window: `last_${checks.hours}h`, label: "Check warnings" }} /> warnings
                <span className="w-full text-xs text-[#aab3c2]">{checks.windowText}</span>
              </>
            ) : (
              "n/a"
            )}
          </dd>
        </div>
      </dl>
      {heartbeat && heartbeat.length ? (
        <div className="mt-4">
          <p className="mb-1 text-sm">
            Capture heartbeat, last 24 hours <span className="text-[#aab3c2]">· our data, not the trains</span>
          </p>
          <Bars bars={heartbeatBars(heartbeat)} />
          <p className="mt-1 flex flex-wrap gap-x-4 text-xs text-[#aab3c2]">
            <span>Bar height: share of one-minute captures that ran</span>
            <span className="flex items-center gap-1"><span aria-hidden="true" className="hatch-outage inline-block h-2.5 w-4" /> Hatched: our data gap, not evaluated</span>
          </p>
        </div>
      ) : null}
      <p className="mt-3 text-sm"><Link href="/pipeline">Pipeline health and checks</Link></p>
    </section>
  );
}
