/** A line with an open incident. Text states it; the icon is decorative. Dashed brown on cream. */
export function DisruptionChip({ open, label }: { open: boolean | null; label?: string }) {
  if (open == null) {
    return (
      <span data-testid="disruption" className="text-xs text-muted">
        Incident status unknown
      </span>
    );
  }
  return open ? (
    <span data-testid="disruption" className="chip-disruption">
      <BusIcon />
      {label ?? "Service disruption"}
    </span>
  ) : (
    <span data-testid="disruption" className="text-xs text-muted">
      No open incident
    </span>
  );
}

/** Stops were skipped in an open incident. Same look as a disruption: it is one. */
export function StopsSkippedChip() {
  return (
    <span data-testid="skipped" className="chip-disruption">
      <span aria-hidden="true">!</span>
      Stops skipped
    </span>
  );
}

export function BusIcon() {
  return (
    <svg aria-hidden="true" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
      <rect x="2" y="2.5" width="12" height="9" rx="2" />
      <path d="M2 7h12M5 13.5v-2M11 13.5v-2" />
      <circle cx="5" cy="9.3" r=".6" fill="currentColor" />
      <circle cx="11" cy="9.3" r=".6" fill="currentColor" />
    </svg>
  );
}
