import { formatNumber, MIN_COVERAGE_PCT } from "@/lib/map";

/** Coverage next to a number. Text carries the meaning; colour only reinforces it. */
export function CoverageBadge({ coverage }: { coverage: number | null | undefined }) {
  const known = coverage != null;
  const low = !known || coverage < MIN_COVERAGE_PCT;
  const label = known ? `coverage ${formatNumber(coverage, 1)}%` : "coverage unknown";
  return (
    <span
      data-testid="coverage-badge"
      data-low={low}
      title="Share of scheduled trips Headway could observe. Under 50% a value is withheld."
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${
        low
          ? "border-amber-700 bg-amber-50 text-amber-900 dark:border-amber-400 dark:bg-amber-950 dark:text-amber-100"
          : "border-emerald-700 bg-emerald-50 text-emerald-900 dark:border-emerald-400 dark:bg-emerald-950 dark:text-emerald-100"
      }`}
    >
      {label}
    </span>
  );
}
