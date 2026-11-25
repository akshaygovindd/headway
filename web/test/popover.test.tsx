import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { MetricPopover } from "@/components/MetricPopover";
import { Metric } from "@/components/trust";

function Page() {
  return (
    <>
      <a href="#x">before</a>
      <Metric
        cell={{ value: 87, coverage_pct: 77 }}
        unit="pct0"
        info={{ k: "within_band", line: "Blue", n: 412, window: "last_24h", excluded: "2 outage hours", label: "Blue Line: evenly spaced" }}
      />
      <Metric cell={{ value: 70, coverage_pct: 68 }} unit="pct0" info={{ k: "on_time", line: "Orange" }} />
      <MetricPopover />
    </>
  );
}

describe("How this was measured popover", () => {
  it("opens with Enter, shows the definition, n, coverage, window, exclusions and links", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const trigger = screen.getByRole("button", { name: "87%" });
    trigger.focus();
    await user.keyboard("{Enter}");
    const dialog = screen.getByRole("dialog", { name: /Blue Line: evenly spaced/ });
    expect(dialog).toHaveAttribute("open");
    expect(dialog).toHaveTextContent("0.5 to 1.5 times the scheduled gap");
    expect(dialog).toHaveTextContent("412");
    expect(dialog).toHaveTextContent("77% of scheduled trips observed");
    expect(dialog).toHaveTextContent("last 24 hours");
    expect(dialog).toHaveTextContent("2 outage hours");
    expect(within(dialog).getByRole("link", { name: /METRICS\.md/ })).toHaveAttribute("href", expect.stringContaining("docs/METRICS.md#8-headway-adherence-release-2"));
    expect(within(dialog).getByRole("link", { name: /The SQL/ })).toHaveAttribute("href", expect.stringContaining("agg_headway_window.sql"));
    expect(dialog).toHaveTextContent("about 4 minutes behind the timetable");
    expect(within(dialog).getByRole("link", { name: "See the measurement" })).toHaveAttribute("href", expect.stringContaining("docs/MEASUREMENTS.md#blue-and-green-e"));
  });

  it("opens with Space and only Blue carries the note", async () => {
    const user = userEvent.setup();
    render(<Page />);
    screen.getByRole("button", { name: "70%" }).focus();
    await user.keyboard(" ");
    const dialog = screen.getByRole("dialog");
    expect(dialog).toHaveTextContent("Share of arrivals");
    expect(dialog).not.toHaveTextContent("4 minutes behind");
  });

  it("moves focus into the dialog, traps Tab and Shift+Tab inside it", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const trigger = screen.getByRole("button", { name: "87%" });
    await user.click(trigger);
    const dialog = screen.getByRole("dialog");
    const items = Array.from(dialog.querySelectorAll<HTMLElement>("a[href], button"));
    expect(items.length).toBeGreaterThanOrEqual(3);
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    for (let i = 0; i < items.length * 2 + 1; i++) {
      await user.tab();
      expect(dialog).toContainElement(document.activeElement as HTMLElement);
    }
    await user.tab({ shift: true });
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it("closes with Escape and returns focus to the number", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const trigger = screen.getByRole("button", { name: "87%" });
    trigger.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("dialog")).toHaveAttribute("open");
    await user.keyboard("{Escape}");
    expect(document.querySelector("dialog")).not.toHaveAttribute("open");
    expect(trigger).toHaveFocus();
  });

  it("closes with the Close button and focus returns", async () => {
    const user = userEvent.setup();
    render(<Page />);
    const trigger = screen.getByRole("button", { name: "70%" });
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(document.querySelector("dialog")).not.toHaveAttribute("open");
    expect(trigger).toHaveFocus();
  });
});
