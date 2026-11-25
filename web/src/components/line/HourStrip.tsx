import { HEALTH_LEGEND, HEALTH_RAMP } from "@/lib/health";
import { hourState, hourText, orderHours, type HourState } from "@/lib/linedata";
import { hourLabel, SERVICE_HOURS } from "@/lib/time";
import type { SpacingHour } from "@/lib/types";

const WORD: Record<HourState, string> = {
  even: "even",
  some_bunching: "some bunching",
  bunched_gaps: "bunched or gaps",
  withheld: "not enough data",
  no_headways: "no headways",
  outage: "our data gap",
};

function Swatch({ state }: { state: HourState }) {
  const cls = state === "outage" ? "hatch-outage" : state === "withheld" ? "hatch-withheld" : state === "no_headways" ? "border border-dashed border-[#566070] bg-card" : "";
  const style = state === "even" || state === "some_bunching" || state === "bunched_gaps" ? { background: HEALTH_RAMP[state] } : undefined;
  return <span aria-hidden="true" className={`block h-6 w-full rounded-[2px] ${cls}`} style={style} />;
}

/**
 * The service day (05:00 to 01:00 ET) by hour: spacing health from the API's hourly shares, coloured
 * with the same ramp as the stations. Our own data gaps are hatched darker; withheld hours (too little
 * coverage) and hours with no valid headway are separate states. Colour never carries the meaning
 * alone: every hour has a word under it, a title and a screen-reader label.
 */
export function HourStrip({ hours, date }: { hours: SpacingHour[]; date?: string }) {
  const byHour = new Map(orderHours(hours).map((h) => [h.hour_local, h]));
  return (
    <div>
      <ol className="grid grid-cols-5 gap-1 sm:grid-cols-10 lg:grid-cols-[repeat(20,minmax(0,1fr))]" data-testid="hour-strip" aria-label={`Spacing by hour${date ? `, ${date}` : ""}, 05:00 to 01:00 Eastern`}>
        {SERVICE_HOURS.map((hr) => {
          const h = byHour.get(hr);
          const state: HourState | "none" = h ? hourState(h) : "none";
          return (
            <li key={hr} data-state={state} title={h ? hourText(h) : `${hourLabel(hr)}: no scheduled headways`} className="rounded px-0.5 text-center text-xs">
              {state === "none" ? <span aria-hidden="true" className="block h-6 w-full rounded-[2px] border border-dashed border-line" /> : <Swatch state={state} />}
              <span className="mt-1 block font-semibold">{hourLabel(hr)}</span>
              <span className="block text-[11px] leading-tight text-muted">{state === "none" ? "no service" : WORD[state]}</span>
              <span className="sr-only">{h ? hourText(h) : `${hourLabel(hr)}: no scheduled headways`}</span>
            </li>
          );
        })}
      </ol>
      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm" aria-label="Legend for spacing by hour" data-testid="hour-legend">
        {HEALTH_LEGEND.map((x) => (
          <li key={x.state} className="flex items-center gap-1.5">
            <span aria-hidden="true" className="inline-block h-3 w-5 rounded-[2px]" style={{ background: HEALTH_RAMP[x.state] }} />
            {x.label}
          </li>
        ))}
        <li className="flex items-center gap-1.5"><span aria-hidden="true" className="hatch-withheld inline-block h-3 w-5 rounded-[2px]" />Not enough data (under 50% coverage)</li>
        <li className="flex items-center gap-1.5"><span aria-hidden="true" className="hatch-outage inline-block h-3 w-5 rounded-[2px]" />Our data gap: capture was down, the hour is left out of every number</li>
        <li className="flex items-center gap-1.5"><span aria-hidden="true" className="inline-block h-3 w-5 rounded-[2px] border border-dashed border-[#566070]" />No headways: trains were scheduled but none was measured</li>
      </ul>
    </div>
  );
}
