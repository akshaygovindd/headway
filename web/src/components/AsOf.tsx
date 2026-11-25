import { Freshness } from "@/components/trust";
import { getApi } from "@/lib/api";
import { UNAVAILABLE_TEXT } from "@/lib/map";
import { hhmmET } from "@/lib/time";
import type { MetaData } from "@/lib/types";

/** The one "as of" for every page, from /v1/meta. The age beside it is worked out in the browser. */
export async function AsOf() {
  const meta = await getApi<MetaData>("/meta");
  if (!meta.ok) return <p className="text-xs text-muted" data-testid="as-of">{UNAVAILABLE_TEXT}</p>;
  const { metrics_as_of, exported_at } = meta.body.freshness;
  const t = hhmmET(metrics_as_of);
  return (
    <p className="text-xs text-muted" data-testid="as-of">
      Data as of {t ? `${t} ET` : "unknown"} · <Freshness exportedAt={exported_at} />
    </p>
  );
}
