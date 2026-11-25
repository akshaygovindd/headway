import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

afterEach(cleanup);

// next/font is compiled away at build time; under vitest the loader returns plain class names.
vi.mock("next/font/local", () => ({
  default: ({ variable }: { variable: string }) => ({ variable: variable.replace("--", "font-var-"), className: "font", style: {} }),
}));
