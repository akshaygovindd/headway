import { render, screen, within } from "@testing-library/react";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ageText } from "@/components/AccessibilityTable";
import { respond } from "../scripts/mock-api.mjs";
import { shell, stubFixtureApi } from "./helpers";

const page = async () => {
  const el = await (await import("@/app/accessibility/page")).default();
  return render(await shell(el));
};
afterEach(() => vi.unstubAllGlobals());

describe("ageText", () => {
  it("says hours, days, months, years", () => {
    expect(ageText(5)).toBe("5 hours");
    expect(ageText(103)).toBe("4 days");
    expect(ageText(1030.5)).toBe("43 days");
    expect(ageText(4698.4)).toBe("6 months");
    expect(ageText(41142)).toBe("4.7 years");
  });
});

describe("/accessibility", () => {
  beforeEach(() => stubFixtureApi());

  it("says how many stations are out now and that these are announcements", async () => {
    await page();
    expect(screen.getByTestId("out-now-summary").textContent).toMatch(/3 of 125 stations/);
    expect(screen.getByText(/announcements, not the equipment's own status/)).toBeInTheDocument();
  });

  it("marks out-now stations in words, with the public alert text, and sorts them first", async () => {
    await page();
    const rows = screen.getAllByTestId("station-row");
    expect(rows.map((r) => r.getAttribute("data-out"))).toEqual(["true", "true", "true", "false", "false"]);
    const out = within(rows[0]!).getByTestId("out-now");
    expect(out.textContent).toMatch(/Out now/);
    expect(out.textContent).toMatch(/Bowdoin Escalator 125/);
    expect(within(rows[3]!).getByText("None announced now")).toBeInTheDocument();
  });

  it("shows an open outage with its real age, and counts elevators and escalators", async () => {
    await page();
    const rows = screen.getAllByTestId("station-row");
    expect(rows[0]!.textContent).toMatch(/4\.7 years/);
    expect(rows[1]!.textContent).toMatch(/3 \(3 escalator\)/); // Alewife: three escalators out
    expect(rows[2]!.textContent).toMatch(/1 \(1 elevator\)/);
  });

  it("outage hours are a popover button with n and window", async () => {
    await page();
    const btn = within(screen.getAllByTestId("station-row")[0]!).getByRole("button");
    expect(btn.getAttribute("data-k")).toBe("outage_hours");
    expect(btn.getAttribute("data-n")).toBe("1");
    expect(btn.getAttribute("data-win")).toBe("last_24h");
  });

  it("has no axe violations", async () => {
    HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;
    const { container } = await page();
    const r = await axe.run(container, { rules: { "color-contrast": { enabled: false } } });
    expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });
});

describe("/accessibility states", () => {
  const stub = (edit: (b: { data: { stations: unknown[]; n_stations_out_now: number } }) => void) =>
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
        const { status, body } = respond(url.pathname, url.search);
        if (url.pathname === "/v1/accessibility") edit(body as never);
        return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      }),
    );

  it("withheld window figures show the hatch and a reason, never a number, but out now stays", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: string | URL | Request) => {
        const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url);
        const u = url.pathname === "/v1/accessibility" ? new URL("http://x/v1/accessibility?window=last_7d") : url;
        const { status, body } = respond(u.pathname, u.search);
        return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
      }),
    );
    await page();
    const row = screen.getAllByTestId("station-row")[0]!;
    expect(row.textContent).toMatch(/Not enough data/);
    expect(within(row).queryByRole("button")).toBeNull();
    expect(row.textContent).toMatch(/Out now/);
    expect(screen.getByText(/some are withheld because there is too little capture coverage/)).toBeInTheDocument();
  });

  it("says nothing is announced, and that this is not a check of the equipment", async () => {
    stub((b) => {
      b.data.stations = [];
      b.data.n_stations_out_now = 0;
    });
    await page();
    expect(screen.getByTestId("none-announced").textContent).toMatch(/not a check of the equipment/);
    expect(screen.getByTestId("out-now-summary").textContent).toMatch(/0 of 125 stations/);
  });
});
