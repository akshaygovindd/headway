/** A line with an open incident. The text states it; the icon is decorative. */
export function DisruptionChip({ open, label }: { open: boolean | null; label?: string }) {
  if (open == null) {
    return (
      <span data-testid="disruption" className="text-xs text-muted">
        Incident status unknown
      </span>
    );
  }
  return open ? (
    <span
      data-testid="disruption"
      className="inline-flex items-center gap-1 rounded-full border border-red-700 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-900 dark:border-red-400 dark:bg-red-950 dark:text-red-100"
    >
      <span aria-hidden="true">!</span>
      {label ?? "Open incident"}
    </span>
  ) : (
    <span data-testid="disruption" className="text-xs text-muted">
      No open incident
    </span>
  );
}
