import { describe, expect, it } from "vitest";
import { checksSummary, checksText, windowText } from "@/lib/checks";
import type { ApiResult } from "@/lib/api";
import type { ChecksData } from "@/lib/types";

const ok = (over: Partial<ChecksData> = {}): ApiResult<ChecksData> => ({
  ok: true,
  body: {
    data: { hours: 24, n_checks: 6, n_failing_checks: 0, n_warning_checks: 0, checks: [], ...over },
    coverage: {} as never,
    freshness: {} as never,
    provenance: {} as never,
  },
});

describe("trust strip checks", () => {
  it("maps zero failing and zero warnings", () => {
    const c = checksSummary(ok())!;
    expect(c).toMatchObject({ failing: 0, warnings: 0, hours: 24, windowText: "last 24 h" });
    expect(checksText(c)).toBe("0 failing · 0 warnings (last 24 h)");
  });

  it("maps failing checks and warnings straight from the API's distinct counts", () => {
    expect(checksText(checksSummary(ok({ n_failing_checks: 2, n_warning_checks: 3 }))!)).toBe("2 failing · 3 warnings (last 24 h)");
    expect(checksSummary(ok({ n_failing_checks: 0, n_warning_checks: 1 }))).toMatchObject({ failing: 0, warnings: 1 });
  });

  it("words the window from hours", () => {
    expect(windowText(6)).toBe("last 6 h");
    expect(windowText(168)).toBe("last 7 days");
  });

  it("is null (shown as n/a) when the endpoint is missing or down", () => {
    expect(checksSummary({ ok: false, kind: "not_available", status: 404, message: "Not published yet" })).toBeNull();
    expect(checksSummary({ ok: false, kind: "unavailable", status: 500, message: "down" })).toBeNull();
  });
});
