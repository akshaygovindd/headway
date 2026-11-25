import { describe, expect, it } from "vitest";
import { coverageState, covText, meterFill } from "@/lib/coverage";

describe("coverage states", () => {
  it.each([
    [null, "unknown"],
    [undefined, "unknown"],
    [0, "withheld"],
    [49.9, "withheld"],
    [50, "partial"],
    [68, "partial"],
    [69.9, "partial"],
    [70, "full"],
    [100, "full"],
  ] as const)("%s is %s", (cov, state) => {
    expect(coverageState(cov)).toBe(state);
  });

  it("fills one meter block per 20%, rounded, and clamps", () => {
    expect([0, 9, 10, 35, 68, 77, 87, 100, 140, -5].map(meterFill)).toEqual([0, 0, 1, 2, 3, 4, 4, 5, 5, 0]);
    expect(meterFill(null)).toBe(0);
  });

  it("writes whole-percent text", () => {
    expect(covText(34.6)).toBe("35% cov.");
    expect(covText(null)).toBe("cov. unknown");
  });
});
