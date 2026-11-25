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

const MIN_INCIDENT_HOURS = 2;
const OUTAGE_THRESHOLDS = new Set(["data_outage", "outage", "no_data", "capture_gap"]);

/**
 * Guard (the API applies the same rule): a gap in Headway's own data is never an incident. A real
 * incident has at least two evaluated hours, names a service threshold, and its evaluated hours
 * plus the outage hours inside it fit in its span.
 */
export function isRealIncident(i: Incident): boolean {
  const crossed = thresholds(i);
  if (crossed.length === 0 || crossed.some((t) => OUTAGE_THRESHOLDS.has(t))) return false;
  if (i.n_hours < MIN_INCIDENT_HOURS) return false;
  if (i.end_ts) {
    const span = (Date.parse(i.end_ts) - Date.parse(i.start_ts)) / 3600000;
    if (Number.isFinite(span) && i.n_hours + (i.n_outage_hours_inside ?? 0) > span + 1e-6) return false;
  }
  return true;
}

export function realIncidents(r: ApiResult<IncidentsData>): Incident[] {
  return r.ok ? r.body.data.incidents.filter(isRealIncident) : [];
}

export function openIncidentsByLine(r: ApiResult<IncidentsData>): Map<string, Incident[]> | null {
  if (!r.ok) return null;
  const out = new Map<string, Incident[]>();
  for (const i of realIncidents(r)) {
    if (!i.is_open) continue;
    out.set(i.line, [...(out.get(i.line) ?? []), i]);
  }
  return out;
}

export function thresholds(i: Incident): string[] {
  const t = i.thresholds_crossed;
  return Array.isArray(t) ? t : t ? [t] : [];
}

/**
 * The short reason a line has no number, for the hero sentence. Only a known coverage under the
 * minimum is "withheld"; no coverage figure at all is missing data, and says so.
 */
export function withheldWhy(cell: Cell, disrupted: boolean): { state: "withheld" | "unavailable"; text: string } {
  const s = shown(cell);
  if (s.kind !== "withheld" || s.coverage == null) return { state: "unavailable", text: "data unavailable right now" };
  const cov = `${Math.round(s.coverage)}% coverage`;
  return { state: "withheld", text: disrupted ? `${cov} during a service disruption` : `${cov}, under the 50% minimum` };
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
