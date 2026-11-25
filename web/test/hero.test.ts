import { describe, expect, it } from "vitest";
import { heroSentence, type HeroLine } from "@/lib/hero";

const L = (line: string, kind: "heavy" | "light", value: number | null, reason?: string): HeroLine => ({ line, kind, value, reason });
const eight = (over: Record<string, number | null> = {}): HeroLine[] => [
  L("Red", "heavy", over.Red ?? 71), L("Orange", "heavy", over.Orange ?? 78), L("Blue", "heavy", over.Blue ?? 87),
  L("Green-B", "light", over["Green-B"] ?? 64), L("Green-C", "light", over["Green-C"] ?? 70),
  L("Green-D", "light", over["Green-D"] ?? 73), L("Green-E", "light", over["Green-E"] ?? 68), L("Mattapan", "light", over.Mattapan ?? 81),
];

describe("heroSentence", () => {
  it("states ranges per group and counts what can be measured", () => {
    expect(heroSentence(eight())).toBe(
      "Right now, 8 of 8 lines can be measured. Heavy-rail trains were evenly spaced 71–87% of the time; Green Line branches and Mattapan were on time 64–81% of the time.",
    );
  });

  it("names withheld lines with their reason and leaves them out of the ranges", () => {
    const s = heroSentence([
      L("Red", "heavy", null, "35% coverage during a shuttle disruption"),
      ...eight().slice(1),
    ]);
    expect(s).toContain("7 of 8 lines can be measured");
    expect(s).toContain("evenly spaced 78–87%");
    expect(s).toMatch(/Red withheld \(35% coverage during a shuttle disruption\)\.$/);
  });

  it("groups lines that share a reason", () => {
    const s = heroSentence([L("Red", "heavy", null, "low coverage"), L("Blue", "heavy", null, "low coverage"), L("Orange", "heavy", 80)]);
    expect(s).toContain("Red and Blue withheld (low coverage)");
  });

  it("collapses a single value to one number", () => {
    expect(heroSentence([L("Orange", "heavy", 68.4), L("Red", "heavy", null)])).toContain("evenly spaced 68% of the time");
  });

  it("handles nothing measurable", () => {
    const s = heroSentence(eight().map((l) => ({ ...l, value: null, reason: "not enough data" })));
    expect(s).toBe("Right now, none of the 8 lines can be measured. Red, Orange, Blue, Green-B, Green-C, Green-D, Green-E and Mattapan withheld (not enough data).");
    expect(s).not.toMatch(/\d%/);
  });

  it("handles only light rail measurable, and no lines at all", () => {
    expect(heroSentence([L("Mattapan", "light", 81)])).toContain("Light-rail lines were on time 81%");
    expect(heroSentence([])).toBe("No lines are reported yet.");
  });

  it("never ranks: no ordering words", () => {
    expect(heroSentence(eight())).not.toMatch(/best|worst|better|worse|highest|lowest|rank/i);
  });
});
