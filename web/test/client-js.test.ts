import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = path.join(dir, f);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}

describe("client JavaScript budget", () => {
  it("only the CSV button, the metric popover and Next's required error boundary are client components", () => {
    const clients = walk(path.join(process.cwd(), "src"))
      .filter((f) => /\.(tsx?|jsx?)$/.test(f))
      .filter((f) => /^\s*["']use client["']/.test(readFileSync(f, "utf8")))
      .map((f) => path.basename(f))
      .sort();
    expect(clients).toEqual(["CsvButton.tsx", "MetricPopover.tsx", "error.tsx"]);
  });
});
