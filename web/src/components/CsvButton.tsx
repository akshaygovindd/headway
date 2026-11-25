"use client";

import { buildCsv, buildMetadata, type CsvSpec } from "@/lib/csv";

function save(text: string, type: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

/** Build the CSV (data only) at click time and save it; the definition and export time are a separate file. */
export function CsvButton({ spec, filename }: { spec: CsvSpec; filename: string }) {
  const meta = filename.replace(/\.csv$/, "") + ".metadata.txt";
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3">
      <button
        type="button"
        onClick={() => save(buildCsv(spec, new Date().toISOString()), "text/csv;charset=utf-8", filename)}
        className="inline-flex min-h-11 items-center rounded border border-line px-4 text-sm"
      >
        Download CSV
      </button>
      <button
        type="button"
        onClick={() => save(buildMetadata(spec, new Date().toISOString(), filename), "text/plain;charset=utf-8", meta)}
        className="inline-flex min-h-11 items-center text-sm underline"
      >
        Metadata
      </button>
    </span>
  );
}
