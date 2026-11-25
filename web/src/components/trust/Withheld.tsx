import { withheldText } from "@/lib/map";

/** What renders instead of a value that may not be shown. Never contains the value. */
export function Withheld({
  coverage,
  reason,
  block = false,
}: {
  coverage: number | null | undefined;
  reason?: string | null;
  /** A larger hatched block, for cards. */
  block?: boolean;
}) {
  return (
    <span
      data-testid="withheld"
      className={`hatch-withheld inline-block rounded text-sm text-[#3A4350] ${block ? "w-full px-3 py-4" : "px-2 py-0.5"}`}
      title={reason ?? undefined}
    >
      {withheldText(coverage)}
    </span>
  );
}
