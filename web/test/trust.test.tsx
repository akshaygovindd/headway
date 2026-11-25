import { act, render, screen } from "@testing-library/react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import {
  CoverageBadge,
  DisruptionChip,
  Freshness,
  Metric,
  OutageLabel,
  Withheld,
} from "@/components/trust";

describe("Metric", () => {
  it("renders a number only together with a coverage badge", () => {
    render(<Metric cell={{ value: 87.46, coverage_pct: 71.8 }} unit="pct" />);
    expect(screen.getByRole("button", { name: "87.5%" })).toBeInTheDocument();
    expect(screen.getByTestId("coverage-badge")).toHaveTextContent("72% cov.");
    expect(screen.getByTestId("coverage-badge")).toHaveAttribute("data-state", "full");
    expect(screen.queryByTestId("withheld")).toBeNull();
  });

  it("renders Not enough data and none of the value when coverage is under 50%", () => {
    const { container } = render(<Metric cell={{ value: 91.23, coverage_pct: 30 }} unit="pct" />);
    expect(screen.getByTestId("withheld")).toHaveTextContent("Not enough data \u00b7 30% cov.");
    expect(container.textContent).not.toMatch(/91/);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByTestId("coverage-badge")).toBeNull();
  });

  it("says data unavailable (not withheld) for a missing value, and withholds a stated reason", () => {
    const a = render(<Metric cell={{ value: null, coverage_pct: 80 }} unit="s" />);
    expect(a.container.textContent).toBe("Data unavailable right now");
    expect(a.container.textContent).not.toMatch(/coverage|Not enough/i);
    a.unmount();
    const b = render(
      <Metric cell={{ value: 42, coverage_pct: 80, withheld_reason: "coverage 30% is below 50%" }} unit="s" />,
    );
    expect(b.container.textContent).not.toMatch(/42/);
  });

  it("is unavailable, not withheld, when there is no coverage figure", () => {
    const { container } = render(<Metric cell={{ value: 12, coverage_pct: null }} unit="s" />);
    expect(container.textContent).toBe("Data unavailable right now");
    expect(screen.getByTestId("unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("withheld")).toBeNull();
  });

  it("shows exactly at the 50% boundary and withholds just under", () => {
    const a = render(<Metric cell={{ value: 10, coverage_pct: 50 }} unit="count" />);
    expect(screen.getByTestId("coverage-badge")).toBeInTheDocument();
    a.unmount();
    render(<Metric cell={{ value: 10, coverage_pct: 49.9 }} unit="count" />);
    expect(screen.getByTestId("withheld")).toBeInTheDocument();
  });

  it("keeps zero as a number", () => {
    render(<Metric cell={{ value: 0, coverage_pct: 95 }} unit="s" />);
    expect(screen.getByRole("button", { name: "0 s" })).toBeInTheDocument();
  });
});

describe("Metric as a popover trigger", () => {
  it("is a button carrying what the popover needs, and a partial value says so", () => {
    render(
      <Metric
        cell={{ value: 70, coverage_pct: 68 }}
        unit="pct"
        info={{ k: "within_band", line: "Orange", n: 412, window: "last_24h", excluded: "2 outage hours" }}
      />,
    );
    const b = screen.getByRole("button", { name: "70.0%" });
    expect(b).toHaveAttribute("data-k", "within_band");
    expect(b).toHaveAttribute("data-n", "412");
    expect(b).toHaveAttribute("data-line", "Orange");
    expect(screen.getByTestId("coverage-badge")).toHaveTextContent("partial");
  });
});

describe("other trust components", () => {
  it("CoverageBadge marks low and unknown coverage", () => {
    const a = render(<CoverageBadge coverage={20} />);
    expect(screen.getByTestId("coverage-badge")).toHaveAttribute("data-low", "true");
    a.unmount();
    render(<CoverageBadge coverage={null} />);
    expect(screen.getByTestId("coverage-badge")).toHaveTextContent("cov. unknown");
  });

  it("Withheld text", () => {
    render(<Withheld coverage={12.34} />);
    expect(screen.getByTestId("withheld")).toHaveTextContent("Not enough data \u00b7 12% cov.");
  });

  it("Freshness is worked out at view time from the export time, not frozen at build", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-11-24T00:06:36Z"));
      const a = render(<Freshness exportedAt="2026-11-23T23:54:06Z" />);
      expect(screen.getByTestId("freshness")).toHaveTextContent("updated 12 min ago");
      expect(screen.getByTestId("freshness")).toHaveAttribute("data-stale", "false");
      // Time passes with the page open: the text moves on by itself.
      act(() => {
        vi.advanceTimersByTime(60 * 60000);
      });
      expect(screen.getByTestId("freshness")).toHaveTextContent("updated 1 h ago (stale)");
      a.unmount();
      // Same markup viewed much later says so: nothing from the build leaks in.
      vi.setSystemTime(new Date("2026-11-24T06:00:00Z"));
      render(<Freshness exportedAt="2026-11-23T23:54:06Z" />);
      expect(screen.getByTestId("freshness")).toHaveTextContent("updated 6 h ago (stale)");
    } finally {
      vi.useRealTimers();
    }
  });

  it("Freshness before the browser clock is known shows the fixed export time only", () => {
    const html = renderToString(<Freshness exportedAt="2026-11-23T23:54:06Z" />);
    expect(html).toContain("exported 18:54 ET");
    expect(html).not.toMatch(/min ago|h ago/);
  });

  it("DisruptionChip states open, none and unknown in words", () => {
    const a = render(<DisruptionChip open={true} />);
    expect(screen.getByTestId("disruption")).toHaveTextContent("Service disruption");
    a.unmount();
    const b = render(<DisruptionChip open={false} />);
    expect(screen.getByTestId("disruption")).toHaveTextContent("No open incident");
    b.unmount();
    render(<DisruptionChip open={null} />);
    expect(screen.getByTestId("disruption")).toHaveTextContent("unknown");
  });

  it("OutageLabel renders only when there are outage hours", () => {
    const a = render(<OutageLabel hours={0} />);
    expect(a.container).toBeEmptyDOMElement();
    a.unmount();
    render(<OutageLabel hours={2} />);
    expect(screen.getByTestId("outage")).toHaveTextContent("2 hours without data");
  });
});
