"use client";

import { useEffect, useState } from "react";
import { ageAt, freshnessText, isStale } from "@/lib/map";
import { hhmmET } from "@/lib/time";

/**
 * "updated N min ago", computed in the browser when the page is viewed (and again every 30 s),
 * from the export time. Never from a number baked in at build time: server output is the fixed
 * clock time of the export, so a cached page cannot claim a stale age.
 */
export function Freshness({ exportedAt }: { exportedAt: string | null | undefined }) {
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 30000);
    return () => clearInterval(id);
  }, []);
  const age = now == null ? null : ageAt(exportedAt, now);
  const stale = now != null && isStale(age);
  const at = hhmmET(exportedAt);
  return (
    <span data-testid="freshness" data-stale={now == null ? undefined : stale} className="text-xs text-muted">
      {exportedAt ? (
        <time dateTime={exportedAt} suppressHydrationWarning>
          {now == null ? (at ? `exported ${at} ET` : "update time unknown") : freshnessText(age)}
        </time>
      ) : (
        "update time unknown"
      )}
      {stale ? " (stale)" : ""}
    </span>
  );
}
