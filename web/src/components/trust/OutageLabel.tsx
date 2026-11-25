/** Hours inside an incident where Headway's own data was missing, so they were not evaluated. */
export function OutageLabel({ hours }: { hours: number }) {
  if (!hours || hours <= 0) return null;
  return (
    <span
      data-testid="outage"
      className="inline-flex items-center rounded border border-slate-600 bg-slate-100 px-2 py-0.5 text-xs text-slate-900 dark:border-slate-400 dark:bg-slate-800 dark:text-slate-100"
      title="Data outage hours are not evaluated: a gap in Headway's data, not a statement about service."
    >
      {hours} {hours === 1 ? "hour" : "hours"} without data (Headway outage)
    </span>
  );
}
