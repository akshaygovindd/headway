import { segmentFill, segmentTitle } from "@/components/home/NetworkDiagram";
import type { Seg } from "@/lib/network";

/** Stations in order with each stretch coloured by spacing health. Horizontal, vertical on a phone. */
export function StationStrip({ segs, label }: { segs: Seg[]; label: string }) {
  if (!segs.length) return <p className="text-sm text-muted">Segment health is not published yet.</p>;
  return (
    <div className="overflow-x-auto">
      <ol className="strip flex gap-0.5 sm:min-w-[40rem]" aria-label={label} data-testid="station-strip">
        {segs.map((s, i) => (
          <li key={`${s.row.from_stop_id}-${s.row.to_stop_id}`} className="strip-item flex min-w-16 flex-1 flex-col gap-1" data-state={s.state}>
            <span
              title={segmentTitle(s)}
              aria-hidden="true"
              className={`strip-bar block h-8 w-full rounded-[2px] ${s.state === "withheld" ? "hatch-withheld" : ""} ${s.row.disrupted ? "band-disruption" : ""}`}
              style={{ background: segmentFill(s.state) }}
            />
            <span className="text-[11px] leading-tight text-muted sm:text-xs">
              {i === 0 ? <span className="block font-semibold text-fg">{s.row.from_stop_name}</span> : null}
              <span className={i === 0 ? "block" : ""}>{s.row.to_stop_name}</span>
              <span className="sr-only">: {segmentTitle(s)}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
