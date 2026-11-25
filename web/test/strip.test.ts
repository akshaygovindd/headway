import { describe, expect, it } from "vitest";
import { collides, flatBoxes, labelPlan, stopNames } from "@/lib/strip";

const segs = (names: string[]) => names.slice(1).map((to, i) => ({ from_stop_name: names[i] as string, to_stop_name: to }));

describe("station strip labels", () => {
  it("names the stop points: one label per station, so the first segment does not show two names", () => {
    const names = stopNames(segs(["A", "B", "C", "D"]));
    expect(names).toEqual(["A", "B", "C", "D"]);
    const plan = labelPlan(names);
    expect(plan.labels.map((l) => l.name)).toEqual(["A", "B", "C", "D"]);
    plan.labels.forEach((l, i) => expect(l.pct).toBeCloseTo((i * 100) / 3, 6));
  });

  it("keeps few stations flat on alternating rows without touching", () => {
    const names = ["Alewife", "Davis", "Porter", "Harvard", "Central"];
    const plan = labelPlan(names, 960);
    expect(plan.mode).toBe("flat");
    expect(collides(flatBoxes(names, 960))).toBe(false);
    expect(new Set(plan.labels.map((l) => l.row))).toEqual(new Set([0, 1]));
  });

  it("angles every label when a long line would collide, and still labels both ends", () => {
    const names = Array.from({ length: 22 }, (_, i) => `Station number ${i + 1}`);
    names[0] = "North Station";
    names[21] = "Cleveland Circle";
    expect(collides(flatBoxes(names, 960))).toBe(true);
    const plan = labelPlan(names, 960);
    expect(plan.mode).toBe("angled");
    expect(plan.labels).toHaveLength(22);
    expect(plan.labels[0]?.name).toBe("North Station");
    expect(plan.labels[21]?.name).toBe("Cleveland Circle");
    // Angled labels need about 14 px of room each along the strip.
    expect(960 / 21).toBeGreaterThan(14);
  });

  it("anchors the end labels inside the strip", () => {
    const plan = labelPlan(["First", "Mid", "Last"], 960);
    expect(plan.labels[0]?.anchor).toBe("start");
    expect(plan.labels[2]?.anchor).toBe("end");
  });
});
