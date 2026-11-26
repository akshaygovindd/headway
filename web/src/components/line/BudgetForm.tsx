import { WINDOW_LABEL } from "@/lib/linedata";
import type { BudgetStation } from "@/lib/types";

/**
 * "How much time should I budget?" picker: two station selects and a window, a plain HTML GET form
 * (no client JavaScript). It submits to the line's budget page, which asks the API for the pair.
 */
export function BudgetForm({
  line,
  stations,
  from,
  to,
  window,
}: {
  line: string;
  stations: BudgetStation[];
  from?: string;
  to?: string;
  window?: string;
}) {
  const select = (name: "from" | "to", label: string, value?: string) => (
    <label className="grid gap-1 text-sm font-semibold" htmlFor={`budget-${name}-${line}`}>
      {label}
      <select
        id={`budget-${name}-${line}`}
        name={name}
        required
        defaultValue={value ?? ""}
        className="min-h-11 w-full rounded-md border border-line bg-card px-2 py-2 text-base font-normal"
      >
        <option value="" disabled>
          Choose a station
        </option>
        {stations.map((s) => (
          <option key={s.id} value={s.id}>
            {s.name}
          </option>
        ))}
      </select>
    </label>
  );
  return (
    <form method="get" action={`/lines/${line}/budget`} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end" data-testid="budget-form">
      {select("from", "From", from)}
      {select("to", "To", to)}
      <label className="grid gap-1 text-sm font-semibold" htmlFor={`budget-window-${line}`}>
        Based on
        <select
          id={`budget-window-${line}`}
          name="window"
          defaultValue={window ?? "last_7d"}
          className="min-h-11 rounded-md border border-line bg-card px-2 py-2 text-base font-normal"
        >
          {Object.entries(WINDOW_LABEL).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </label>
      <button type="submit" className="min-h-11 rounded-md border border-fg bg-card px-4 py-2 text-base font-semibold">
        Show the budget
      </button>
    </form>
  );
}
