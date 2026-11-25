import { freshnessText, isStale } from "@/lib/map";

export function Freshness({
  ageMinutes,
  exportedAt,
}: {
  ageMinutes: number | null | undefined;
  exportedAt?: string | null;
}) {
  const stale = isStale(ageMinutes);
  return (
    <span data-testid="freshness" data-stale={stale} className="text-xs text-muted">
      {exportedAt ? <time dateTime={exportedAt}>{freshnessText(ageMinutes)}</time> : freshnessText(ageMinutes)}
      {stale ? " (stale)" : ""}
    </span>
  );
}
