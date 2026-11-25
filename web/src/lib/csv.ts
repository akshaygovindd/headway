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
  if (s.kind === "unavailable") return "data unavailable";
  if (s.reason) return s.reason;
  return `${coverageText(s.coverage)} is below the minimum for showing a value`;
}

/**
 * The API sends a skipped-stop share as a fraction (0.013). A CSV cell says what it holds: shares
 * are written as percent points with unit "pct", like every other percentage.
 */
export function csvValue(value: number, unit: Unit): { value: number; unit: Unit } {
  return unit === "share" ? { value: round(value * 100, 4), unit: "pct" } : { value, unit };
}

/**
 * The CSV is data only: a header row, then rows. The definition link and export time are in a
 * separate metadata file (buildMetadata), so spreadsheet and pandas imports never meet a comment.
 */
export function buildCsv(spec: CsvSpec, exportedAt: string): string {
  void exportedAt;
  const eol = "\r\n";
  const lines: string[] = [[...spec.labelColumns, ...VALUE_COLUMNS].map(csvField).join(",")];
  for (const row of spec.rows) {
    const s = shown(row.cell);
    const out = s.kind === "value" ? csvValue(s.value, row.unit) : null;
    const fields = [
      ...spec.labelColumns.map((c) => csvField(row.labels[c])),
      out ? String(round(out.value, 6)) : "",
      csvField(out ? out.unit : row.unit === "share" ? "pct" : row.unit),
      row.cell.coverage_pct == null ? "" : String(round(row.cell.coverage_pct, 2)),
      csvField(withheldReason(row.cell)),
      csvField(spec.freshness),
    ];
    lines.push(fields.join(","));
  }
  return lines.join(eol) + eol;
}

/** The plain-text companion of a CSV: where the metric is defined, when it was exported, the rule. */
export function buildMetadata(spec: CsvSpec, exportedAt: string, filename: string): string {
  return (
    [
      `file: ${filename}`,
      `metric_definition: ${methodologyUrl(spec.definition)}`,
      `exported_at: ${exportedAt}`,
      `data_freshness (exported_at of the gold data): ${spec.freshness ?? "unknown"}`,
      `rows: ${spec.rows.length}`,
      "columns: " + [...spec.labelColumns, ...VALUE_COLUMNS].join(", "),
      "rule: a value from a row whose coverage is under 50% is left empty, with the reason in withheld_reason; percentages are percent points (a skipped-stop share of 0.013 is written 1.3)",
    ].join("\r\n") + "\r\n"
  );
}
