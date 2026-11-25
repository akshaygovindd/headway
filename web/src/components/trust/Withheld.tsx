import { withheldText } from "@/lib/map";

/** What renders instead of a value that may not be shown. Never contains the value. */
export function Withheld({
  coverage,
  reason,
}: {
  coverage: number | null | undefined;
  reason?: string | null;
}) {
  return (
    <span data-testid="withheld" className="text-sm italic text-muted" title={reason ?? undefined}>
      {withheldText(coverage)}
    </span>
  );
}
