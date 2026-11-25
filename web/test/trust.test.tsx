import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
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
    expect(screen.getByText("87.5%")).toBeInTheDocument();
    expect(screen.getByTestId("coverage-badge")).toHaveTextContent("coverage 71.8%");
    expect(screen.queryByTestId("withheld")).toBeNull();
  });

  it("renders Not enough data and none of the value when coverage is under 50%", () => {
    const { container } = render(<Metric cell={{ value: 91.23, coverage_pct: 30 }} unit="pct" />);
    expect(screen.getByTestId("withheld")).toHaveTextContent("Not enough data: coverage 30.0%");
    expect(container.textContent).not.toMatch(/91/);
    expect(screen.queryByTestId("coverage-badge")).toBeNull();
  });

  it("withholds a null value and a value with a stated reason", () => {
    const a = render(<Metric cell={{ value: null, coverage_pct: 80 }} unit="s" />);
    expect(a.container.textContent).toBe("Not enough data: coverage 80.0%");
    a.unmount();
    const b = render(
      <Metric cell={{ value: 42, coverage_pct: 80, withheld_reason: "coverage 30% is below 50%" }} unit="s" />,
    );
    expect(b.container.textContent).not.toMatch(/42/);
  });

  it("withholds when coverage is missing", () => {
    const { container } = render(<Metric cell={{ value: 12, coverage_pct: null }} unit="s" />);
    expect(container.textContent).toBe("Not enough data: coverage unknown");
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
    expect(screen.getByText("0 s")).toBeInTheDocument();
  });
});

describe("other trust components", () => {
  it("CoverageBadge marks low and unknown coverage", () => {
    const a = render(<CoverageBadge coverage={20} />);
    expect(screen.getByTestId("coverage-badge")).toHaveAttribute("data-low", "true");
    a.unmount();
    render(<CoverageBadge coverage={null} />);
    expect(screen.getByTestId("coverage-badge")).toHaveTextContent("coverage unknown");
  });

  it("Withheld text", () => {
    render(<Withheld coverage={12.34} />);
    expect(screen.getByTestId("withheld")).toHaveTextContent("Not enough data: coverage 12.3%");
  });

  it("Freshness says N min ago and flags stale data", () => {
    const a = render(<Freshness ageMinutes={12.7} exportedAt="2026-11-23T23:54:06Z" />);
    expect(screen.getByTestId("freshness")).toHaveTextContent("updated 12 min ago");
    expect(screen.getByTestId("freshness")).toHaveAttribute("data-stale", "false");
    a.unmount();
    render(<Freshness ageMinutes={400} />);
    expect(screen.getByTestId("freshness")).toHaveTextContent("updated 6 h ago (stale)");
  });

  it("DisruptionChip states open, none and unknown in words", () => {
    const a = render(<DisruptionChip open={true} />);
    expect(screen.getByTestId("disruption")).toHaveTextContent("Open incident");
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
