// WCAG 2.x contrast of every text/background pair the site renders. jsdom cannot compute layout, so
// axe's colour-contrast rule is off in the a11y tests; this test replaces it with an explicit table.
// Light tokens come from src/app/globals.css (:root); a few status colours are literals in
// components and are listed here with the file that uses them. A new token must be classified below
// or the "every token is classified" test fails, so colours cannot be added without a contrast check.
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { HEALTH_RAMP } from "@/lib/health";
import { LINE_STYLE } from "@/lib/lines";

const css = readFileSync(`${process.cwd()}/src/app/globals.css`, "utf8");
const root = /:root\s*{([^}]*)}/.exec(css)?.[1] ?? "";
export const TOKENS: Record<string, string> = Object.fromEntries(
  [...root.matchAll(/(--[a-z-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\s*;/g)].map((m) => [m[1]!, m[2]!.toLowerCase()]),
);

function rgb(hex: string): [number, number, number] {
  let h = hex.replace("#", "");
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  if (!/^[0-9a-fA-F]{6}$/.test(h)) throw new Error(`not a hex colour: ${hex}`);
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

export function luminance(hex: string): number {
  const [r, g, b] = rgb(hex).map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  }) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

/** `var(--name)` resolves against globals.css; anything else must be a hex literal. */
const val = (v: string): string => {
  const m = /^var\((--[a-z-]+)\)$/.exec(v);
  if (!m) return v.toLowerCase();
  const t = TOKENS[m[1]!];
  if (!t) throw new Error(`token ${m[1]} is not defined in globals.css :root`);
  return t;
};

type Pair = { use: string; fg: string; bg: string[]; large?: boolean };

/** Large text: 24 px or more, or bold from 18.66 px (WCAG 1.4.3): needs 3:1, everything else 4.5:1. */
const PAIRS: Pair[] = [
  { use: "body text", fg: "var(--fg)", bg: ["var(--bg)", "var(--card)", "#f4f5f7" /* popover note box */] },
  { use: "hero heading (24 to 36 px, 800)", fg: "var(--fg)", bg: ["var(--bg)"], large: true },
  { use: "large metric number (30 px, 800)", fg: "var(--fg)", bg: ["var(--card)"], large: true },
  { use: "muted text, captions, coverage badge", fg: "var(--muted)", bg: ["var(--bg)", "var(--card)", "#f4f5f7"] },
  { use: "links and focus text", fg: "var(--accent)", bg: ["var(--bg)", "var(--card)"] },
  { use: "disruption chip and stretch label", fg: "var(--disrupt-ink)", bg: ["var(--disrupt-bg)", "var(--card)"] },
  { use: "selected 24 h / 7 d toggle (text-white on bg-fg)", fg: "#ffffff", bg: ["var(--fg)"] },
  { use: "popover dialog text", fg: "var(--fg)", bg: ["#ffffff"] },
  { use: "metric button hover", fg: "var(--fg)", bg: ["#e8ebf0"] },
  { use: "partial coverage word (CoverageBadge)", fg: "#92400e", bg: ["var(--card)", "var(--bg)"] },
  { use: "withheld hatch text (Withheld)", fg: "#3a4350", bg: ["#dde1e7", "#f4f5f7"] },
  { use: "outage hatch text (OutageLabel)", fg: "#1f2630", bg: ["#9aa3af", "#cdd2da"] },
  { use: "Marey axis labels", fg: "#566070", bg: ["var(--card)"] },
  { use: "trust strip text", fg: "#f4f5f7", bg: ["var(--dark)"] },
  { use: "trust strip labels and legend", fg: "#aab3c2", bg: ["var(--dark)"] },
  { use: "trust strip links", fg: "#c9d3ff", bg: ["var(--dark)"] },
  // Line badges: the ink each badge actually uses (src/lib/lines.ts).
  ...Object.entries(LINE_STYLE).map(([line, s]) => ({ use: `${line} badge (${s.code}, 10 to 18 px, 800)`, fg: s.ink, bg: [s.fill] })),
];

type Row = { use: string; fg: string; bg: string; ratio: number; need: number };
const rows: Row[] = PAIRS.flatMap((p) =>
  p.bg.map((b) => ({ use: p.use, fg: val(p.fg), bg: val(b), ratio: contrast(val(p.fg), val(b)), need: p.large ? 3 : 4.5 })),
);

describe("contrast function", () => {
  it("matches the WCAG reference values", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#ffffff", "#ffffff")).toBeCloseTo(1, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2); // the classic just-failing grey
    expect(contrast("#767676", "#ffffff")).toBeGreaterThanOrEqual(4.5);
    expect(contrast("#fff", "#000")).toBeCloseTo(21, 5); // short hex
  });

  it("is symmetric", () => {
    expect(contrast("#10141a", "#f4f5f7")).toBeCloseTo(contrast("#f4f5f7", "#10141a"), 10);
  });

  it("catches a bad pair: the check fails below the threshold and names the pair", () => {
    const bad: Row = { use: "pale grey on white", fg: "#aaaaaa", bg: "#ffffff", ratio: contrast("#aaaaaa", "#ffffff"), need: 4.5 };
    const failures = [bad].filter((r) => r.ratio < r.need);
    expect(failures).toHaveLength(1);
    expect(fmt(failures)).toContain("pale grey on white");
    expect(fmt(failures)).toContain("2.32");
    // The same pair would pass as large text (3:1) only if it reached 3:1, which it does not.
    expect(contrast("#aaaaaa", "#ffffff")).toBeLessThan(3);
  });
});

const fmt = (rs: Row[]) => rs.map((r) => `${r.use}: ${r.fg} on ${r.bg} = ${r.ratio.toFixed(2)}:1, needs ${r.need}:1`).join("\n");

describe("site colour pairs", () => {
  it("reads the light tokens from globals.css", () => {
    expect(TOKENS["--fg"]).toBe("#10141a");
    expect(TOKENS["--muted"]).toBe("#566070");
    expect(TOKENS["--disrupt-ink"]).toBe("#7a2e00");
    expect(TOKENS["--disrupt-bg"]).toBe("#fff1e3");
  });

  it("keeps the brand line colours", () => {
    expect(Object.fromEntries(["Red", "Orange", "Blue", "Green-B", "Mattapan"].map((l) => [l, LINE_STYLE[l]!.fill.toUpperCase()]))).toEqual({
      Red: "#DA291C", Orange: "#ED8B00", Blue: "#003DA5", "Green-B": "#00843D", Mattapan: "#DA291C",
    });
  });

  it.each(rows.map((r) => [`${r.use}: ${r.fg} on ${r.bg}`, r] as const))("%s", (_name, r) => {
    expect(r.ratio, `contrast too low\n${fmt([r])}`).toBeGreaterThanOrEqual(r.need);
  });

  it("passes every pair (one readable list if any fails)", () => {
    const failing = rows.filter((r) => r.ratio < r.need);
    expect(failing, `contrast failures:\n${fmt(failing)}`).toEqual([]);
  });

  it("classifies every token in :root, so a new colour cannot skip the check", () => {
    const textTokens = new Set(PAIRS.flatMap((p) => [p.fg, ...p.bg]).flatMap((v) => (/^var\((--[a-z-]+)\)$/.exec(v) ? [/--[a-z-]+/.exec(v)![0]] : [])));
    // Fills, borders and chart colours that never carry text; the ramp is checked as a graphic below.
    const nonText = new Set(["--border", "--even", "--some", "--bunched"]);
    const unclassified = Object.keys(TOKENS).filter((t) => !textTokens.has(t) && !nonText.has(t));
    expect(unclassified, `tokens with no contrast pair: ${unclassified.join(", ")}`).toEqual([]);
  });

  it("keeps the spacing ramp (graphics, no text on it) at 3:1 against the card", () => {
    const low = Object.entries(HEALTH_RAMP).filter(([, c]) => contrast(c, "#ffffff") < 3);
    expect(low, `ramp colours under 3:1 on white: ${low.map(([k, c]) => `${k} ${c} = ${contrast(c, "#ffffff").toFixed(2)}`).join(", ")}`).toEqual([]);
    expect(HEALTH_RAMP.even.toLowerCase()).toBe(TOKENS["--even"]);
    expect(HEALTH_RAMP.some_bunching.toLowerCase()).toBe(TOKENS["--some"]);
    expect(HEALTH_RAMP.bunched_gaps.toLowerCase()).toBe(TOKENS["--bunched"]);
  });
});
