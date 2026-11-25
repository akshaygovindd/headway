import { BusIcon } from "@/components/trust";

/** The five states a number or a stretch can be in. Each row: a fixed icon column, then name and words. */
export function StateKey() {
  const items = [
    { sample: <span className="cov-meter" aria-hidden="true">{[1, 1, 1, 1, 1].map((_, i) => <span key={i} data-on="true" />)}</span>, name: "Shown", text: "70% or more of scheduled trips observed. The number is shown with its coverage." },
    { sample: <span aria-hidden="true" className="inline-block h-2.5 w-2.5 rounded-full bg-[#D97706]" />, name: "Partial", text: "50 to 70% observed. Shown, marked with an amber dot and the word \u201cpartial\u201d." },
    { sample: <span aria-hidden="true" className="hatch-withheld inline-block h-4 w-8 rounded-sm" />, name: "Withheld", text: "Under 50% observed. We show \u201cNot enough data\u201d and the coverage instead of a number." },
    { sample: <span aria-hidden="true" className="hatch-outage inline-block h-4 w-8 rounded-sm" />, name: "Our data gap", text: "Headway\u2019s own capture was down. Those hours are left out; it says nothing about the trains." },
    { sample: <span aria-hidden="true" className="band-disruption inline-flex h-5 items-center rounded-sm px-1"><BusIcon /></span>, name: "Service disruption", text: "An alert says service was disrupted. Dashed outline, bus icon and words." },
  ];
  return (
    <ul className="grid gap-x-8 gap-y-4 sm:grid-cols-2" data-testid="state-key" aria-label="How to read Headway">
      {items.map((x) => (
        <li key={x.name} className="grid grid-cols-[3.5rem_minmax(0,1fr)] items-start gap-x-3">
          <span className="flex h-6 items-center" data-testid="state-icon">{x.sample}</span>
          <span>
            <span className="block font-semibold">{x.name}</span>
            <span className="block text-sm text-muted">{x.text}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
