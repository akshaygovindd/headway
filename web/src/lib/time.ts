// All times on the site are Eastern. Inputs are UTC ISO strings from the API.

const TZ = "America/New_York";
const hm = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const hourOnly = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "2-digit", hourCycle: "h23" });

export function hhmmET(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : hm.format(d);
}

export function hourET(iso: string): number {
  return Number(hourOnly.format(new Date(iso))) % 24;
}

/** The service day runs 05:00 to 01:00: hours 5..23, then 0. */
export const SERVICE_HOURS: number[] = [...Array.from({ length: 19 }, (_, i) => i + 5), 0];

export function hourLabel(h: number): string {
  return `${String(h).padStart(2, "0")}:00`;
}

const day = new Intl.DateTimeFormat("en-US", { timeZone: TZ, month: "short", day: "numeric", year: "numeric" });

/** "Nov 24, 2026, 08:00": date, then the 24-hour time, Eastern. */
export function dateTimeET(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : `${day.format(d)}, ${hm.format(d)}`;
}
