import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { shell } from "./helpers";

function nav(): HTMLElement[] {
  return Array.from(screen.getByRole("navigation", { name: "Main" }).querySelectorAll("a"));
}

describe("keyboard-only navigation", () => {
  it("tabs through skip link, brand, then every main-nav link in order", async () => {
    const user = userEvent.setup();
    render(await shell(<p>content</p>));
    const order = [screen.getByRole("link", { name: "Skip to content" }), screen.getByRole("link", { name: "Headway" }), ...nav()];
    expect(order.map((l) => l.textContent)).toEqual([
      "Skip to content", "Headway", "Lines", "Accuracy", "Incidents", "Slow zones", "Elevators", "Forecast", "Pipeline", "Methodology", "GitHub",
    ]);
    for (const el of order) {
      await user.tab();
      expect(el).toHaveFocus();
    }
  });

  it("has the v2 navigation and the disclaimer in the header", async () => {
    render(await shell(<p>content</p>));
    expect(screen.getAllByRole("link", { name: "Methodology" })[0]).toHaveAttribute("href", "/how-we-measure");
    expect(screen.getByRole("link", { name: "Accuracy" })).toHaveAttribute("href", "/#accuracy");
    expect(screen.getByText("Independent project · not affiliated with the MBTA")).toBeInTheDocument();
    expect(screen.getByText(/Times in ET/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Data contracts" })).toHaveAttribute("href", "/data");
  });

  it("activates a nav link with Enter", async () => {
    const user = userEvent.setup();
    render(await shell(<p>content</p>));
    const link = screen.getByRole("link", { name: "Incidents" });
    link.focus();
    let clicked = false;
    link.addEventListener("click", (e) => {
      clicked = true;
      e.preventDefault();
    });
    await user.keyboard("{Enter}");
    expect(clicked).toBe(true);
  });

  it("moves focus backwards with Shift+Tab", async () => {
    const user = userEvent.setup();
    render(await shell(<p>content</p>));
    screen.getByRole("link", { name: "Lines" }).focus();
    await user.tab({ shift: true });
    expect(screen.getByRole("link", { name: "Headway" })).toHaveFocus();
  });
});
