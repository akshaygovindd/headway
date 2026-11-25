/** Hours inside an incident where Headway's own data was missing, so they were not evaluated. */
export function OutageLabel({ hours }: { hours: number }) {
  if (!hours || hours <= 0) return null;
  return (
    <span
      data-testid="outage"
      className="hatch-outage inline-flex items-center rounded px-2 py-0.5 text-xs text-[#1F2630]"
      title="Data outage hours are not evaluated: a gap in Headway's data, not a statement about service."
    >
      {hours} {hours === 1 ? "hour" : "hours"} without data (our data gap)
    </span>
  );
}
