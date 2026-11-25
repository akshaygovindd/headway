import { annotateMarey, layoutMarey, type MareyLayout, type PathStop } from "@/lib/marey";
import { hhmmET } from "@/lib/time";
import type { MareyRow } from "@/lib/types";

function paths(l: MareyLayout) {
  const solid: string[] = [];
  const dashed: string[] = [];
  const faint: string[] = [];
  for (const t of l.trips) {
    for (const run of t.runs) {
      run.slice(1).forEach((p, i) => {
        const q = run[i]!;
        (p.method === "estimated" || q.method === "estimated" ? dashed : solid).push(`M${q.x.toFixed(1)} ${q.y.toFixed(1)}L${p.x.toFixed(1)} ${p.y.toFixed(1)}`);
      });
    }
    // The timetable is drawn only where the train was: never across a shuttle stretch.
    if (t.timetable.length > 1 && !l.band) faint.push("M" + t.timetable.map((p) => `${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join("L"));
  }
  return { solid: solid.join(""), dashed: dashed.join(""), faint: faint.join("") };
}

function Svg({ uid, rows, hourStart, orientation, minutes, label, annotations, path, covered }: {
  uid: string;
  path?: PathStop[];
  covered?: Set<string>;
  rows: MareyRow[];
  hourStart: number;
  orientation: "across" | "down";
  minutes: number;
  label: string;
  annotations: ReturnType<typeof annotateMarey>;
}) {
  const across = orientation === "across";
  const l = layoutMarey(rows, {
    orientation,
    t0: hourStart + (60 - minutes) * 60000,
    t1: hourStart + 3600000,
    timeLength: across ? 760 : 440,
    stopGap: across ? 22 : 30,
    labelSize: across ? 170 : 130,
    path,
    covered,
  });
  const p = paths(l);
  const clip = `clip-${uid}-${orientation}`;
  return (
    <svg viewBox={`0 0 ${l.width} ${l.height}`} width={l.width} height={l.height} role="img" aria-label={label} className="max-w-none" style={{ fontSize: 11 }}>
      <clipPath id={clip}><rect x={l.plot.x} y={l.plot.y} width={l.plot.w} height={l.plot.h} /></clipPath>
      {l.ticks.map((t) => (
        <g key={t.label}>
          {across ? <line x1={t.x} x2={t.x} y1={l.plot.y} y2={l.plot.y + l.plot.h} stroke="#E3E6EB" /> : <line x1={l.plot.x} x2={l.plot.x + l.plot.w} y1={t.y} y2={t.y} stroke="#E3E6EB" />}
          <text x={across ? t.x : l.plot.x - 6} y={across ? l.plot.y - 6 : t.y + 4} textAnchor={across ? "middle" : "end"} fill="#566070">{t.label}</text>
        </g>
      ))}
      {l.stops.map((s) =>
        across ? (
          <g key={s.id}>
            <line x1={l.plot.x} x2={l.plot.x + l.plot.w} y1={s.y} y2={s.y} stroke="#EEF0F3" />
            <text x={l.plot.x - 8} y={s.y + 4} textAnchor="end" fill="#10141A">{s.name}</text>
          </g>
        ) : (
          <g key={s.id}>
            <line x1={s.x} x2={s.x} y1={l.plot.y} y2={l.plot.y + l.plot.h} stroke="#EEF0F3" />
            <text transform={`translate(${s.x + 4} ${l.plot.y - 8}) rotate(-60)`} fill="#10141A">{s.name}</text>
          </g>
        ),
      )}
      {l.band ? (
        <g data-kind="shuttle" data-testid="shuttle-band">
          <rect
            x={across ? l.plot.x : l.band.from}
            y={across ? l.band.from : l.plot.y}
            width={across ? l.plot.w : l.band.to - l.band.from}
            height={across ? l.band.to - l.band.from : l.plot.h}
            fill="#EEF0F3"
            stroke="#A8B0BC"
            strokeDasharray="4 3"
          />
          <text
            x={across ? l.plot.x + l.plot.w / 2 : (l.band.from + l.band.to) / 2}
            y={across ? (l.band.from + l.band.to) / 2 + 4 : l.plot.y + l.plot.h / 2}
            textAnchor="middle"
            fill="#566070"
            fontWeight={600}
            transform={across ? undefined : `rotate(90 ${(l.band.from + l.band.to) / 2} ${l.plot.y + l.plot.h / 2})`}
          >
            Shuttle buses
          </text>
        </g>
      ) : null}
      <g clipPath={`url(#${clip})`} fill="none">
        <path d={p.faint} stroke="#A8B0BC" strokeWidth={1} strokeDasharray="2 4" data-kind="timetable" />
        <path d={p.solid} stroke="#10141A" strokeWidth={2} data-kind="observed" />
        <path d={p.dashed} stroke="#10141A" strokeWidth={2} strokeDasharray="6 4" data-kind="estimated" />
      </g>
      {annotations.map((a, i) => {
        const pos = l.position(a.at, a.stop_id);
        if (!pos) return null;
        return (
          <g key={`${a.kind}-${a.stop_id}`} data-kind={a.kind}>
            <circle cx={pos.x} cy={pos.y} r={9} fill={a.kind === "bunching" ? "#C93A8B" : "#fff"} stroke="#C93A8B" strokeWidth={2} />
            <text x={pos.x} y={pos.y + 4} textAnchor="middle" fill={a.kind === "bunching" ? "#fff" : "#C93A8B"} fontWeight={700}>{i + 1}</text>
          </g>
        );
      })}
    </svg>
  );
}

const mins = (s: number) => `${Math.round(s / 60)} min`;

/**
 * Time-distance chart of the last complete hour in one direction: stations down, time across;
 * observed solid, estimated dashed, the timetable faint. On a phone: the last 30 minutes with
 * time running down, and a link that opens the full chart. Server-rendered SVG, no script.
 */
export function MareyChart({ rows, hourStart, toward, path, covered, scored = true }: {
  rows: MareyRow[];
  hourStart: string;
  toward: string;
  path?: PathStop[];
  /** Stops inside a line-scope disruption: shaded as "Shuttle buses", no train drawn through. */
  covered?: Set<string>;
  /** False for a withheld line: the chart is shown as observations only, with no notes. */
  scored?: boolean;
}) {
  const uid = toward.replace(/[^A-Za-z0-9]+/g, "-").toLowerCase();
  const t0 = Date.parse(hourStart);
  const annotations = scored ? annotateMarey(rows.filter((r) => !covered?.has(r.stop_id))) : [];
  const label = `Marey chart, trains toward ${toward}, ${hhmmET(hourStart)} to ${hhmmET(new Date(t0 + 3600000).toISOString())} ET. ${new Set(rows.map((r) => r.trip_id)).size} trains.`;
  return (
    <figure data-testid="marey">
      <div id={`marey-full-${uid}`} className="marey-desktop overflow-x-auto" data-testid="marey-desktop">
        <Svg uid={uid} rows={rows} hourStart={t0} orientation="across" minutes={60} label={label} annotations={annotations} path={path} covered={covered} />
        <p className="mt-1 text-xs sm:hidden"><a href={`#marey-${uid}`}>Back to the last 30 minutes</a></p>
      </div>
      <div className="marey-phone overflow-x-auto" id={`marey-${uid}`} data-testid="marey-phone">
        <Svg uid={uid} rows={rows} hourStart={t0} orientation="down" minutes={30} label={`${label} Last 30 minutes.`} annotations={annotations} path={path} covered={covered} />
        <p className="mt-2 text-sm"><a href={`#marey-full-${uid}`} className="inline-block py-2">Open full chart (last hour)</a></p>
      </div>
      <figcaption className="mt-3 grid gap-1 text-sm text-muted">
        <span className="flex flex-wrap gap-x-5 gap-y-1">
          <span><svg width="26" height="8" aria-hidden="true"><line x1="0" x2="26" y1="4" y2="4" stroke="#10141A" strokeWidth="2" /></svg> Observed</span>
          <span><svg width="26" height="8" aria-hidden="true"><line x1="0" x2="26" y1="4" y2="4" stroke="#10141A" strokeWidth="2" strokeDasharray="6 4" /></svg> Estimated (between observed stops)</span>
          <span><svg width="26" height="8" aria-hidden="true"><line x1="0" x2="26" y1="4" y2="4" stroke="#A8B0BC" strokeDasharray="2 4" /></svg> Timetable</span>
        </span>
        {!scored ? (
          <span data-testid="marey-unscored">Observations only, not scored: too few of this line&rsquo;s trains were captured to judge bunching or long waits.</span>
        ) : annotations.length ? (
          <ol className="mt-1 list-decimal pl-5 text-fg" aria-label="Annotations" data-testid="marey-notes">
            {annotations.map((a) => (
              <li key={`${a.kind}-${a.stop_id}`}>
                {a.kind === "bunching" ? "Bunching" : "Long wait"} at {a.stop_name}, {hhmmET(a.at)}: {mins(a.gap_s)} between trains (usually {mins(a.expected_s)}).
              </li>
            ))}
          </ol>
        ) : (
          <span>No bunching or long waits stood out in this hour.</span>
        )}
      </figcaption>
    </figure>
  );
}
