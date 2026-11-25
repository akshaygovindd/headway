import { segmentFill, segmentTitle } from "@/components/home/NetworkDiagram";
import { labelPlan, stopNames } from "@/lib/strip";
import type { Seg } from "@/lib/network";

const bar = (s: Seg) => `strip-bar block rounded-[2px] ${s.state === "withheld" ? "hatch-withheld" : ""} ${s.row.disrupted ? "band-disruption" : ""}`;

/**
 * Stations in order with each stretch coloured by spacing health. Names are at the stop points
 * between stretches (angled when they would collide), so every station, both ends included, has
 * exactly one label. On a phone it is a vertical list of stops with the stretch between them.
 */
export function StationStrip({ segs, label }: { segs: Seg[]; label: string }) {
  if (!segs.length) return <p className="text-sm text-muted">Segment health is not published yet.</p>;
  const names = stopNames(segs.map((s) => s.row));
  const plan = labelPlan(names);
  const flat = plan.mode === "flat";
  return (
    <>
      <div className="strip-desktop overflow-x-auto">
        <div className="relative min-w-[40rem] px-1" style={{ paddingBottom: flat ? "3.25rem" : "8.5rem" }} data-testid="strip-box" data-mode={plan.mode}>
          <ol className="strip flex gap-0.5" aria-label={label} data-testid="station-strip">
            {segs.map((s) => (
              <li key={`${s.row.from_stop_id}-${s.row.to_stop_id}`} className="strip-item min-w-0 flex-1" data-state={s.state}>
                <span title={segmentTitle(s)} aria-hidden="true" className={`${bar(s)} h-8 w-full`} style={{ background: segmentFill(s.state) }} />
                <span className="sr-only">{s.row.from_stop_name} to {s.row.to_stop_name}: {segmentTitle(s)}</span>
              </li>
            ))}
          </ol>
          <ul aria-hidden="true" className="pointer-events-none" data-testid="strip-labels">
            {plan.labels.map((l) => (
              <li
                key={l.index}
                data-testid="strip-label"
                data-index={l.index}
                className="absolute whitespace-nowrap text-xs leading-none text-fg"
                style={{
                  left: `calc(0.25rem + (100% - 0.5rem) * ${l.pct / 100})`,
                  top: flat ? `${2.25 + l.row * 1.25}rem` : "2.4rem",
                  transform: flat
                    ? l.anchor === "start" ? "none" : l.anchor === "end" ? "translateX(-100%)" : "translateX(-50%)"
                    : l.anchor === "end" ? "rotate(-45deg)" : "rotate(45deg)",
                  transformOrigin: flat ? undefined : l.anchor === "end" ? "100% 0" : "0 0",
                }}
              >
                {flat && l.row === 1 ? <span className="absolute -top-3 left-1/2 h-3 w-px bg-line" /> : null}
                {l.name}
              </li>
            ))}
          </ul>
        </div>
      </div>
      <ol className="strip-phone" aria-label={label} data-testid="station-strip-phone">
        {segs.map((s, i) => (
          <li key={`${s.row.from_stop_id}-${s.row.to_stop_id}`} data-state={s.state}>
            <p className="flex items-center gap-3 text-sm font-medium"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-full bg-fg" />{names[i]}</p>
            <p className="flex items-stretch gap-3">
              <span aria-hidden="true" title={segmentTitle(s)} className={`${bar(s)} ml-[3px] h-8 w-1`} style={{ background: segmentFill(s.state) }} />
              <span className="self-center text-xs text-muted">{segmentTitle(s)}</span>
            </p>
          </li>
        ))}
        <li>
          <p className="flex items-center gap-3 text-sm font-medium"><span aria-hidden="true" className="h-2.5 w-2.5 rounded-full bg-fg" />{names[names.length - 1]}</p>
        </li>
      </ol>
    </>
  );
}
