import { render } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NotFound from "@/app/not-found";
import { PAGES, shell, stubFixtureApi } from "./helpers";

beforeEach(() => {
  stubFixtureApi();
  // axe probes canvas for some checks; jsdom has none and logs noise.
  HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
});
afterEach(() => vi.unstubAllGlobals());

// jsdom has no layout engine, so colour contrast cannot be computed here; it is a design-token
// decision (tokens in globals.css) not covered by this test.
async function violations(root: Element) {
  const r = await axe.run(root, { rules: { "color-contrast": { enabled: false } } });
  return r.violations.map((v) => `${v.id}: ${v.help} -> ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
}

describe("axe harness", () => {
  it("does detect a real violation", async () => {
    const { container } = render(<button type="button" />);
    expect((await violations(container)).join()).toContain("button-name");
  });
});

describe("axe: every page inside the real layout", () => {
  for (const page of [...PAGES, { name: "not found", load: async () => NotFound() }]) {
    it(page.name, async () => {
      const el = await page.load();
      // The layout renders <html>; mount only its chrome plus the page by rendering into a wrapper.
      const { container } = render(await shell(el));
      expect(await violations(container)).toEqual([]);
    });
  }
});

