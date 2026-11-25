/**
 * A line with an open incident. The text states it; the icon is decorative. `label` is plain text
 * (an MBTA alert header is shown as given, never as HTML), cut off visually by CSS with the full
 * text in the title and in the accessible name.
 */
export function DisruptionChip({ open, label }: { open: boolean | null; label?: string }) {
  if (open == null) {
    return (
      <span data-testid="disruption" className="text-xs text-muted">
        Incident status unknown
      </span>
    );
  }
  const text = label ?? "Service disruption";
  return open ? (
    <span data-testid="disruption" className="chip-disruption max-w-full" title={text}>
      <BusIcon />
      <span className="truncate">{text}</span>
    </span>
  ) : (
    <span data-testid="disruption" className="text-xs text-muted">
      No open incident
    </span>
  );
}

/** Shown only when no alert text is available: stops skipped inferred from an open incident. */
export function StopsSkippedChip({ label = "Possible stop skipped (inferred from incidents)" }: { label?: string }) {
  return (
    <span data-testid="skipped" className="chip-disruption max-w-full" title={label}>
      <span aria-hidden="true">!</span>
      <span className="truncate">{label}</span>
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
