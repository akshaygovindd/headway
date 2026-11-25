"use client";

import { useSyncExternalStore } from "react";
import { ageAt, freshnessText, isStale } from "@/lib/map";
import { hhmmET } from "@/lib/time";

/**
 * "updated N min ago", computed in the browser when the page is viewed (and again every 30 s),
 * from the export time. Never from a number baked in at build time: server output is the fixed
 * clock time of the export, so a cached page cannot claim a stale age.
 */
const TICK_MS = 30000;
const subscribe = (cb: () => void) => {
  const id = setInterval(cb, TICK_MS);
  return () => clearInterval(id);
};
// The snapshot is the current 30 s tick: stable between renders, null on the server.
const clientTick = () => Math.floor(Date.now() / TICK_MS) * TICK_MS;
const serverTick = () => null;

export function Freshness({ exportedAt }: { exportedAt: string | null | undefined }) {
  const now = useSyncExternalStore(subscribe, clientTick, serverTick);
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
