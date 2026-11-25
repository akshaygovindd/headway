import type { Metadata } from "next";
import { pageMeta } from "@/lib/site";
import { Metric } from "@/components/trust";
import { Card, ErrorState, PageHeader, Table, Td } from "@/components/ui";
import { getApi } from "@/lib/api";
import { GATE_TEXT, gateProgress, LINES } from "@/lib/map";
import type { ForecastData } from "@/lib/types";

export const revalidate = 300;
export const metadata: Metadata = pageMeta("Forecast", "Next-hour arrival forecasts for each MBTA subway line, compared with simple baselines and the MBTA's own predictions.", "/forecast");

const METHODS = [
  ["persistence", "Persistence"],
  ["daytype_hour", "Typical for this hour"],
  ["rolling_median_60m", "Last 60 minutes"],
  ["mbta_reference", "MBTA reference"],
] as const;

export default async function ForecastPage() {
  const results = await Promise.all(LINES.map((l) => getApi<ForecastData>(`/forecast/${l}`)));
  return (
    <>
      <PageHeader
        title="Forecast"
        intro="Next-hour delay by direction, from simple baselines. The learned model is shown only after it passes a training gate."
      />
      <p className="mb-6 rounded-lg border border-line bg-card p-4 font-medium" data-testid="gate-text">
        {GATE_TEXT}
      </p>
      <div className="grid gap-4">
        {LINES.map((line, i) => {
          const r = results[i];
          if (!r) return null;
          return (
            <Card key={line} title={`${line}`}>
              {!r.ok ? (
                <ErrorState failure={r} what="The forecast" />
              ) : (
                <>
                  <p className="mb-3 text-sm text-muted">
                    Status: {r.body.data.status ?? "unknown"}
                    {r.body.data.training_gate[0] ? `. Gate: ${gateProgress(r.body.data.training_gate[0])}.` : "."}
                  </p>
                  <Table caption={`Forecast delay in seconds, ${line} line`} head={["Direction", "Minutes ahead", ...METHODS.map(([, l]) => l)]}>
                    {r.body.data.horizons.map((h) => (
                      <tr key={`${h.direction_id}-${h.horizon_min}`}>
                        <Td>{h.direction_id}</Td>
                        <Td>{h.horizon_min}</Td>
                        {METHODS.map(([key]) => (
                          <Td key={key}>
                            <Metric
                              cell={{
                                value: h[key],
                                coverage_pct: h[`${key}_coverage_pct`],
                                withheld_reason: h.withheld_reasons[key] ?? null,
                              }}
                              unit="s"
                            />
                          </Td>
                        ))}
                      </tr>
                    ))}
                  </Table>
                </>
              )}
            </Card>
          );
        })}
      </div>
    </>
  );
}
