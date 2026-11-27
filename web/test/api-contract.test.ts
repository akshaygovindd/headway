// The site's response types (src/lib/types.ts) against the ones generated from the FastAPI OpenAPI
// schema (src/lib/api-types.ts). Compile-time checks: `npm run typecheck` fails when the API sends
// something a site type does not accept: a field the site requires that the API does not always
// send, or one sent with another type. Extra fields the API sends are fine.
import { describe, expect, it } from "vitest";
import type { paths } from "@/lib/api-types";
import type {
  AccessibilityData, AccuracyData, BudgetData, ChecksData, CountdownData, DisruptionsData, Envelope, ForecastData,
  HealthData, HeadwayData, HeartbeatData, IncidentsData, LineSummary, MareyData, MetaData, MetricDef, ReliabilityData,
  SegmentsData, SloData, SlowZonesData, SpacingData, TripTimeData,
} from "@/lib/types";

type Answer<P extends keyof paths> = paths[P] extends { get: { responses: { 200: { content: { "application/json": infer B } } } } } ? B : never;

/** Compile-time only: `Api` must be assignable to the `Site` type, or this file does not type-check. */
const accepts = <Site>() => <Api extends Site>() => true;

describe("the site's types accept what the API's schema says it sends", () => {
  it("covers every typed route (checked by the compiler)", () => {
    accepts<Envelope<LineSummary[]>>()<Answer<"/v1/lines">>();
    accepts<Envelope<ReliabilityData>>()<Answer<"/v1/lines/{line}/reliability">>();
    accepts<Envelope<AccuracyData>>()<Answer<"/v1/lines/{line}/accuracy">>();
    accepts<Envelope<HeadwayData>>()<Answer<"/v1/lines/{line}/headways">>();
    accepts<Envelope<CountdownData>>()<Answer<"/v1/lines/{line}/countdown">>();
    accepts<Envelope<SegmentsData>>()<Answer<"/v1/lines/{line}/segments">>();
    accepts<Envelope<MareyData>>()<Answer<"/v1/lines/{line}/marey">>();
    accepts<Envelope<TripTimeData>>()<Answer<"/v1/lines/{line}/trip-time">>();
    accepts<Envelope<BudgetData>>()<Answer<"/v1/lines/{line}/trip-time-budget">>();
    accepts<Envelope<SpacingData>>()<Answer<"/v1/lines/{line}/spacing-by-hour">>();
    accepts<Envelope<DisruptionsData>>()<Answer<"/v1/lines/{line}/disruptions">>();
    accepts<Envelope<SlowZonesData>>()<Answer<"/v1/slow-zones">>();
    accepts<Envelope<AccessibilityData>>()<Answer<"/v1/accessibility">>();
    accepts<Envelope<IncidentsData>>()<Answer<"/v1/incidents">>();
    accepts<Envelope<ForecastData>>()<Answer<"/v1/forecast/{line}">>();
    accepts<Envelope<HealthData>>()<Answer<"/v1/pipeline/health">>();
    accepts<Envelope<HeartbeatData>>()<Answer<"/v1/pipeline/heartbeat">>();
    accepts<Envelope<ChecksData>>()<Answer<"/v1/pipeline/checks">>();
    accepts<Envelope<SloData>>()<Answer<"/v1/slo">>();
    accepts<Envelope<MetricDef[]>>()<Answer<"/v1/metrics">>();
    accepts<Envelope<MetaData>>()<Answer<"/v1/meta">>();
    expect(true).toBe(true);
  });
});
