// The generated sentence at the top of the home page. Pure and unit tested.
// It reports ranges for groups that can be measured and names the lines that cannot. It never
// orders lines against each other: a line with thin coverage is not compared with one without.

export type HeroLine = {
  line: string;
  kind: "heavy" | "light";
  /** The line's primary metric, 0-100, or null when withheld. */
  value: number | null;
  /** Why it is withheld, in a few words. Used only when value is null. */
  reason?: string | null;
  /** "unavailable" is missing data (no coverage figure), not a withheld value: it never says "withheld". */
  state?: "withheld" | "unavailable";
};

const dash = "–";

function range(values: number[]): string {
  const lo = Math.round(Math.min(...values));
  const hi = Math.round(Math.max(...values));
  return lo === hi ? `${lo}%` : `${lo}${dash}${hi}%`;
}

function nameList(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export function heroSentence(lines: HeroLine[]): string {
  const total = lines.length;
  const ok = lines.filter((l) => l.value != null && Number.isFinite(l.value));
  const withheld = lines.filter((l) => !ok.includes(l));
  const heavy = ok.filter((l) => l.kind === "heavy").map((l) => l.value as number);
  const light = ok.filter((l) => l.kind === "light").map((l) => l.value as number);

  const parts: string[] = [];
  if (total === 0) return "No lines are reported yet.";
  if (ok.length === 0) {
    parts.push(`Right now, none of the ${total} lines can be measured.`);
  } else {
    parts.push(`Right now, ${ok.length} of ${total} lines can be measured.`);
    const clauses: string[] = [];
    if (heavy.length) clauses.push(`Heavy-rail trains were evenly spaced ${range(heavy)} of the time`);
    if (light.length) {
      clauses.push(`${heavy.length ? "Green Line branches and Mattapan" : "Light-rail lines"} were on time ${range(light)} of the time`);
    }
    parts.push(`${clauses.join("; ")}.`);
  }
  if (withheld.length) {
    // Group lines that share a reason so the sentence stays short.
    const groups = new Map<string, string[]>();
    for (const l of withheld.filter((x) => x.state !== "unavailable")) {
      const k = l.reason?.trim() || "not enough data";
      groups.set(k, [...(groups.get(k) ?? []), l.line]);
    }
    const text = [...groups].map(([reason, names]) => `${nameList(names)} withheld (${reason})`);
    const missing = withheld.filter((x) => x.state === "unavailable").map((x) => x.line);
    if (missing.length) text.push(`Data unavailable right now for ${nameList(missing)}`);
    parts.push(`${text.join("; ")}.`);
  }
  return parts.join(" ");
}

/** The hero as runs of text: the figures (49%, 80–95%, 3 of 8) are bold, everything else is regular. */
export function heroRuns(text: string): { text: string; bold: boolean }[] {
  const out: { text: string; bold: boolean }[] = [];
  const re = /\d+(?:\u2013\d+)?%|\d+ of \d+/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), bold: false });
    out.push({ text: m[0], bold: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last), bold: false });
  return out;
}
