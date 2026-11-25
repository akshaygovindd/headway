import { coverageState, covText, meterFill } from "@/lib/coverage";

/**
 * The coverage glyph: a five-segment meter and "N% cov." under a number. Partial coverage
 * (50 to under 70) adds an amber dot and the word "partial"; the text carries the meaning and
 * the colour only reinforces it.
 */
export function CoverageBadge({ coverage }: { coverage: number | null | undefined }) {
  const state = coverageState(coverage);
  const fill = meterFill(coverage);
  return (
    <span
      data-testid="coverage-badge"
      data-state={state}
      data-low={state === "withheld" || state === "unknown"}
      title="Share of scheduled trips Headway could observe. Under 50% a value is withheld."
      className="inline-flex flex-wrap items-center gap-x-1.5 text-xs font-medium text-muted"
    >
      <span className="cov-meter" aria-hidden="true">
        {Array.from({ length: 5 }, (_, i) => (
          <span key={i} data-on={i < fill} />
        ))}
      </span>
      <span>{covText(coverage)}</span>
      {state === "partial" ? (
        <span className="inline-flex items-center gap-1 text-[#92400E]">
          <span aria-hidden="true" className="inline-block h-2 w-2 rounded-full bg-[#D97706]" />
          partial
        </span>
      ) : null}
    </span>
  );
}
