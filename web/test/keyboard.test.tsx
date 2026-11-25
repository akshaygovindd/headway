import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import RootLayout from "@/app/layout";

function Shell() {
  const tree = RootLayout({ children: <p>content</p> }) as React.ReactElement<{ children: React.ReactNode[] }>;
  const body = (tree.props.children as React.ReactElement<{ children: React.ReactNode }>[]).find((c) => c.type === "body")!;
  return <div>{body.props.children}</div>;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("keyboard-only navigation", () => {
  it("tabs through skip link, brand, every main-nav link in order, then the theme toggle", async () => {
    const user = userEvent.setup();
    render(<Shell />);
    const order = [
      screen.getByRole("link", { name: "Skip to content" }),
      screen.getByRole("link", { name: "Headway" }),
      ...within(screen.getByRole("navigation", { name: "Main" })).map((l) => l),
      screen.getByRole("button", { name: /theme/i }),
    ];
    expect(order.length).toBeGreaterThanOrEqual(10);
    for (const el of order) {
      await user.tab();
      expect(el).toHaveFocus();
    }
  });

  it("includes the Data page link in the nav", () => {
    render(<Shell />);
    expect(screen.getByRole("link", { name: "Data" })).toHaveAttribute("href", "/data");
  });

  it("activates a nav link with Enter", async () => {
    const user = userEvent.setup();
    render(<Shell />);
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

  it("cycles the theme with Enter and Space and keeps focus on the toggle", async () => {
    const user = userEvent.setup();
    render(<Shell />);
    const toggle = screen.getByRole("button", { name: /theme/i });
    toggle.focus();
    const before = toggle.textContent;
    await user.keyboard("{Enter}");
    const afterEnter = screen.getByRole("button", { name: /theme/i }).textContent;
    expect(afterEnter).not.toBe(before);
    await user.keyboard(" ");
    expect(screen.getByRole("button", { name: /theme/i }).textContent).not.toBe(afterEnter);
    expect(screen.getByRole("button", { name: /theme/i })).toHaveFocus();
    expect(localStorage.getItem("theme")).not.toBeNull();
  });

  it("moves focus backwards with Shift+Tab", async () => {
    const user = userEvent.setup();
    render(<Shell />);
    screen.getByRole("button", { name: /theme/i }).focus();
    await user.tab({ shift: true });
    expect(screen.getByRole("link", { name: "About" })).toHaveFocus();
  });
});

function within(nav: HTMLElement) {
  return Array.from(nav.querySelectorAll("a"));
}
