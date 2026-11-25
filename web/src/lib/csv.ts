// CSV export for exactly the rows a page shows. Pure: no DOM, no clock (the caller passes the
// export time). A withheld value is an empty cell with the reason beside it, never a number.

import { coverageText, methodologyUrl, round, shown, type Cell, type Unit } from "./map";

export type CsvRow = {
  /** Identifying columns for the row (metric name, station, incident id, ...). */
  labels: Record<string, string | number | boolean | null>;
  cell: Cell;
  unit: Unit;
};

export type CsvSpec = {
  /** Names of the label columns, in order. Every row must provide each one. */
  labelColumns: string[];
  rows: CsvRow[];
  /** METRICS.md anchor or docs path of the metric definition. */
  definition: string;
  /** exported_at of the data being shown (freshness). */
  freshness: string | null;
};

export const VALUE_COLUMNS = ["value", "unit", "coverage_pct", "withheld_reason", "freshness"] as const;

/** Quote per RFC 4180 and neutralise spreadsheet formulas in text. */
export function csvField(v: string | number | boolean | null | undefined): string {
  if (v == null) return "";
  let s = String(v);
  if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** Why a value is empty: the API's reason, else the site's own coverage rule. */
export function withheldReason(cell: Cell): string {
  const s = shown(cell);
  if (s.kind === "value") return "";
  if (s.reason) return s.reason;
  if (cell.value == null) return `no value; ${coverageText(s.coverage)}`;
  return `${coverageText(s.coverage)} is below the minimum for showing a value`;
}

export function buildCsv(spec: CsvSpec, exportedAt: string): string {
  const eol = "\r\n";
  const lines: string[] = [
    `# metric definition: ${methodologyUrl(spec.definition)}; exported ${exportedAt}`,
    [...spec.labelColumns, ...VALUE_COLUMNS].map(csvField).join(","),
  ];
  for (const row of spec.rows) {
    const s = shown(row.cell);
    const fields = [
      ...spec.labelColumns.map((c) => csvField(row.labels[c])),
      s.kind === "value" ? String(round(s.value, 6)) : "",
      csvField(row.unit),
      row.cell.coverage_pct == null ? "" : String(round(row.cell.coverage_pct, 2)),
      csvField(withheldReason(row.cell)),
      csvField(spec.freshness),
    ];
    lines.push(fields.join(","));
  }
  return lines.join(eol) + eol;
}
