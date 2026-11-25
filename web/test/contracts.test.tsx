import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import DataPage from "@/app/data/page";
import contracts from "@/data/contracts.json";

const root = path.resolve(__dirname, "..", "..");

describe("contracts.json", () => {
  it("matches what the generator produces from the repo sources", () => {
    expect(() =>
      execFileSync("node", ["scripts/build-contracts.mjs", "--check"], { cwd: path.join(root, "web"), stdio: "pipe" }),
    ).not.toThrow();
  });

  it("points every table at a real doc and anchor", () => {
    const heads = [...fs.readFileSync(path.join(root, "docs/METRICS.md"), "utf8").matchAll(/^#{1,6}\s+(.+)$/gm)].map((m) =>
      m[1]!.trim().toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s/g, "-"),
    );
    for (const t of contracts.tables) {
      const [file, anchor] = t.definition.split("#");
      expect(fs.existsSync(path.join(root, file!))).toBe(true);
      if (anchor) expect(heads).toContain(anchor);
      expect(t.grain).not.toBe("");
      expect(t.endpoints.length).toBeGreaterThan(0);
    }
  });
});

describe("/data page", () => {
  it("renders every table with its columns and labels derived types", () => {
    render(<DataPage />);
    for (const t of contracts.tables) {
      expect(screen.getByRole("heading", { level: 2, name: t.name })).toBeInTheDocument();
    }
    expect(screen.getByTestId("type-note")).toHaveTextContent("derived (naming convention)");
    expect(screen.getByTestId("type-note")).toHaveTextContent("export manifest");
    expect(screen.getAllByRole("table")).toHaveLength(contracts.tables.length);
  });
});
