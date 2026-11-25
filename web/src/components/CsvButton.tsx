"use client";

import { buildCsv, type CsvSpec } from "@/lib/csv";

/** The only client code besides the theme toggle: build the CSV at click time and save it. */
export function CsvButton({ spec, filename }: { spec: CsvSpec; filename: string }) {
  function download() {
    const blob = new Blob([buildCsv(spec, new Date().toISOString())], { type: "text/csv;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <button type="button" onClick={download} className="rounded border border-line px-3 py-1 text-sm">
      Download CSV
    </button>
  );
}
