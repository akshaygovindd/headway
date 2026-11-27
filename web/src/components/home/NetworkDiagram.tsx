import Link from "next/link";
import { LineBadge } from "@/components/LineBadge";
import { BusIcon } from "@/components/trust";
import { HEALTH_RAMP, STATE_LABEL, type SegmentState } from "@/lib/health";
import { lineName } from "@/lib/lines";
import { cleanHeader, disruptedStretches, stateCounts, type Seg, type Stretch } from "@/lib/network";
import { covText } from "@/lib/coverage";

export function segmentFill(state: SegmentState): string | undefined {
  return state === "withheld" ? undefined : HEALTH_RAMP[state];
}

export function segmentTitle(s: Seg): string {
  const r = s.row;
  const head = `${r.from_stop_name} to ${r.to_stop_name}: ${STATE_LABEL[s.state]}`;
  const more = s.state === "withheld" ? `, ${covText(r.coverage_pct)}` : `, ${Math.round(r.pct_within_band ?? 0)}% within the band, ${covText(r.coverage_pct)}`;
  return head + more + (r.disrupted ? " (service disruption)" : "");
}

/** One bar for a segment. Withheld segments are hatched, never filled with a health colour. */
export function SegmentBar({ s, tall = false }: { s: Seg; tall?: boolean }) {
  return (
    <span
      title={segmentTitle(s)}
      data-state={s.state}
      data-disrupted={s.row.disrupted}
      aria-hidden="true"
      className={`block min-w-3.5 flex-1 rounded-[2px] ${tall ? "h-8" : "h-5"} ${s.state === "withheld" ? "hatch-withheld" : ""}`}
      style={{ background: segmentFill(s.state) }}
    />
  );
}

/** The alert text for a stretch (its segments' own header, else the line's), as given; else the stops. */
function stretchLabel(st: Stretch, segs: Seg[], header: string | null | undefined): string {
  const own = segs.slice(st.start, st.end + 1).map((s) => cleanHeader(s.row.disruption_header)).find(Boolean);
  const text = own ?? header;
  return text ? `Service disruption: ${text}` : `Service disruption: ${st.from} to ${st.to}`;
}

/** One row per line, segments between consecutive stops; disrupted stretches are dashed with a bus icon. */
export function NetworkDiagram({ rows }: { rows: { line: string; segs: Seg[]; header?: string | null }[] }) {
  return (
    <ul className="grid gap-3" aria-label="Spacing along each line">
      {rows.map(({ line, segs, header }) => {
        const stretches = disruptedStretches(segs);
        const counts = stateCounts(segs);
        const items: React.ReactNode[] = [];
        let i = 0;
        while (i < segs.length) {
          const st = stretches.find((x) => x.start === i);
          if (st) {
            items.push(
              <span key={`d${i}`} data-testid="stretch" className="band-disruption flex rounded p-0.5" style={{ flex: st.end - st.start + 1 }}>
                <span className="flex flex-1 gap-0.5">
                  {segs.slice(st.start, st.end + 1).map((s, k) => <SegmentBar key={k} s={s} />)}
                </span>
              </span>,
            );
            i = st.end + 1;
          } else {
            items.push(<SegmentBar key={i} s={segs[i] as Seg} />);
            i += 1;
          }
        }
        return (
          <li key={line} className="grid items-center gap-x-4 gap-y-1 sm:grid-cols-[11rem_1fr]">
            <Link href={`/lines/${line}`} className="flex min-h-11 items-center gap-2 font-semibold text-fg no-underline">
              <LineBadge line={line} size={26} />
              {lineName(line)}
            </Link>
            <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={`${lineName(line)} stations, scrollable`}>
              {segs.length ? (
                <div className="flex min-w-[28rem] gap-0.5 py-0.5" data-testid="network-row">{items}</div>
              ) : (
                <p className="text-sm text-muted">Segment health is not published yet.</p>
              )}
              {stretches.map((st) => (
                <p key={st.start} className="mt-1 flex min-w-0 items-center gap-1 text-xs font-medium text-[#7A2E00]" data-testid="stretch-label" title={stretchLabel(st, segs, header)}>
                  <BusIcon />
                  <span className="truncate">{stretchLabel(st, segs, header)}</span>
                </p>
              ))}
              <p className="sr-only">
                {lineName(line)}: {counts.even} evenly spaced, {counts.some_bunching} with some bunching, {counts.bunched_gaps} bunched or with gaps,{" "}
                {counts.withheld} without enough data.
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

export function RampLegend() {
  const items: { state: SegmentState; text: string }[] = [
    { state: "even", text: "Evenly spaced" },
    { state: "some_bunching", text: "Some bunching" },
    { state: "bunched_gaps", text: "Bunched or gaps" },
    { state: "withheld", text: "Not enough data" },
  ];
  return (
    <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-sm" aria-label="Legend">
      {items.map((x) => (
        <li key={x.state} className="flex items-center gap-1.5">
          <span aria-hidden="true" className={`inline-block h-3 w-5 rounded-[2px] ${x.state === "withheld" ? "hatch-withheld" : ""}`} style={{ background: segmentFill(x.state) }} />
          {x.text}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <span aria-hidden="true" className="band-disruption inline-block h-3 w-5 rounded-[2px]" />
        Service disruption
      </li>
    </ul>
  );
}
