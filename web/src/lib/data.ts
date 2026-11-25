import { getApi } from "./api";
import { isHeavyRail, type Cell } from "./map";
import type {
  AccuracyData,
  CountdownData,
  HeadwayData,
  HeadwayRow,
  Incident,
  IncidentsData,
  ReliabilityData,
  ActualRow,
} from "./types";

export const NONE: Cell = { value: null, coverage_pct: null };

export function headwayCell(row: HeadwayRow | undefined, field: keyof HeadwayRow = "pct_within_band"): Cell {
  if (!row) return NONE;
  return { value: row[field] as number | null, coverage_pct: row.coverage_pct, withheld_reason: row.withheld_reason };
}

export function onTimeCell(row: ActualRow | undefined): Cell {
  if (!row) return NONE;
  return { value: row.pct_on_time_window, coverage_pct: row.coverage_pct, withheld_reason: row.withheld_reason };
}

/** The one number a line is summarised by: headway adherence on heavy rail, else on-time share. */
export async function headline(line: string) {
  if (isHeavyRail(line)) {
    const r = await getApi<HeadwayData>(`/lines/${line}/headways?window=last_24h`);
    if (!r.ok) return { ok: false as const, label: "Trains within the expected spacing" };
    return {
      ok: true as const,
      label: "Trains within the expected spacing",
      cell: headwayCell(r.body.data.headway_adherence?.rows[0]),
      freshness: r.body.freshness,
    };
  }
  const r = await getApi<ReliabilityData>(`/lines/${line}/reliability?window=last_24h`);
  if (!r.ok) return { ok: false as const, label: "Arrivals on time" };
  return {
    ok: true as const,
    label: "Arrivals on time",
    cell: onTimeCell(r.body.data.actual_deviation?.rows[0]),
    freshness: r.body.freshness,
  };
}

export async function openIncidentLines(): Promise<Set<string> | null> {
  const r = await getApi<IncidentsData>("/incidents");
  if (!r.ok) return null;
  return new Set(r.body.data.incidents.filter((i: Incident) => i.is_open).map((i) => i.line));
}

export async function lineSections(line: string) {
  const [h24, h7, rel24, rel7, acc, cd] = await Promise.all([
    getApi<HeadwayData>(`/lines/${line}/headways?window=last_24h`),
    getApi<HeadwayData>(`/lines/${line}/headways?window=last_7d`),
    getApi<ReliabilityData>(`/lines/${line}/reliability?window=last_24h`),
    getApi<ReliabilityData>(`/lines/${line}/reliability?window=last_7d`),
    getApi<AccuracyData>(`/lines/${line}/accuracy?window=last_24h`),
    getApi<CountdownData>(`/lines/${line}/countdown?window=last_24h`),
  ]);
  return { h24, h7, rel24, rel7, acc, cd };
}
