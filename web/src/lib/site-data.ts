// Server-side loaders for the home and line pages. Each tolerates a missing endpoint: an
// ApiResult is returned and the page shows what it can.

import { getApi, type ApiResult } from "./api";
import { headwayCell, onTimeCell } from "./data";
import { lineKind } from "./lines";
import { LINES, shown, type Cell } from "./map";
import { orderedSegments, type Seg } from "./network";
import type {
  AccuracyData,
  ChecksData,
  DisruptionsData,
  HeadwayData,
  HeartbeatData,
  Incident,
  IncidentsData,
  LineSummary,
  MetaData,
  ReliabilityData,
  SegmentsData,
} from "./types";

export type LineStat = {
  line: string;
  kind: "heavy" | "light";
  cell: Cell;
  n: number | null;
  label: string;
  /** The key into lib/definitions.ts for the primary number. */
  k: "within_band" | "on_time";
  ok: boolean;
};

export async function lineStat(line: string): Promise<LineStat> {
  const kind = lineKind(line);
  if (kind === "heavy") {
    const r = await getApi<HeadwayData>(`/lines/${line}/headways?window=last_24h`);
    const row = r.ok ? r.body.data.headway_adherence?.rows[0] : undefined;
    return { line, kind, cell: headwayCell(row), n: row?.n_observations ?? null, label: "Evenly spaced", k: "within_band", ok: r.ok };
  }
  const r = await getApi<ReliabilityData>(`/lines/${line}/reliability?window=last_24h`);
  const row = r.ok ? r.body.data.actual_deviation?.rows[0] : undefined;
  return { line, kind, cell: onTimeCell(row), n: row?.n_observations ?? null, label: "On time", k: "on_time", ok: r.ok };
}

export async function segmentsOf(line: string, window: "last_24h" | "last_7d"): Promise<{ result: ApiResult<SegmentsData>; segs: Seg[]; all: SegmentsData["rows"] }> {
  const result = await getApi<SegmentsData>(`/lines/${line}/segments?window=${window}`);
  const all = result.ok ? result.body.data.rows : [];
  return { result, segs: orderedSegments(all, 0), all };
}

export function openIncidentsByLine(r: ApiResult<IncidentsData>): Map<string, Incident[]> | null {
  if (!r.ok) return null;
  const out = new Map<string, Incident[]>();
  for (const i of r.body.data.incidents) {
    if (!i.is_open) continue;
    out.set(i.line, [...(out.get(i.line) ?? []), i]);
  }
  return out;
}

export function thresholds(i: Incident): string[] {
  const t = i.thresholds_crossed;
  return Array.isArray(t) ? t : t ? [t] : [];
}

/** The short reason a line is withheld, for the hero sentence. */
export function withheldWhy(cell: Cell, disrupted: boolean): string {
  const s = shown(cell);
  const cov = s.kind === "withheld" && s.coverage != null ? `${Math.round(s.coverage)}% coverage` : "too little coverage";
  return disrupted ? `${cov} during a service disruption` : `${cov}, under the 50% minimum`;
}

export async function homeData() {
  const [meta, lines, incidents, heartbeat, checks] = await Promise.all([
    getApi<MetaData>("/meta"),
    getApi<LineSummary[]>("/lines"),
    getApi<IncidentsData>("/incidents"),
    getApi<HeartbeatData>("/pipeline/heartbeat?hours=24"),
    getApi<ChecksData>("/pipeline/checks?hours=24"),
  ]);
  const perLine = await Promise.all(
    LINES.map(async (line) => ({
      line,
      stat: await lineStat(line),
      seg: await segmentsOf(line, "last_24h"),
      acc: await getApi<AccuracyData>(`/lines/${line}/accuracy?window=last_24h`),
      disruptions: await getApi<DisruptionsData>(`/lines/${line}/disruptions`),
    })),
  );
  return { meta, lines, incidents, heartbeat, checks, perLine };
}
