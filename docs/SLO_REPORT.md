# SLO report

Generated 2026-11-28 20:34 UTC from `https://headway-public-381232271655.s3.us-east-1.amazonaws.com/public/boston/gold`; metrics as of 2026-11-28T20:00:00Z.
Definitions and targets are in [SLO.md](SLO.md).

## Last 7 and 30 days

| Objective | Window | Target | Actual | Result | Error budget used | Days measured |
| --- | --- | --- | --- | --- | --- | --- |
| Capture completeness | last 30d | 99.00% | 79.46% | **missed** | 2054% | 7 |
| Capture completeness | last 7d | 99.00% | 79.46% | **missed** | 2054% | 7 |
| Gold freshness (at most 90 min old) | last 30d | 99.00% | 100.00% | met | 0% | 5 |
| Gold freshness (at most 90 min old) | last 7d | 99.00% | 100.00% | met | 0% | 5 |
| Transform success | last 30d | 99.00% | 98.00% | **missed** | 200% | 5 |
| Transform success | last 7d | 99.00% | 98.00% | **missed** | 200% | 5 |
| Arrival coverage | last 30d | 80.00% | 77.30% | **missed** | 114% | 7 |
| Arrival coverage | last 7d | 80.00% | 77.30% | **missed** | 114% | 7 |
| Arrival coverage under a service disruption (separate) | last 30d | 80.00% | 12.87% | **missed** | 436% | 1 |
| Arrival coverage under a service disruption (separate) | last 7d | 80.00% | 12.87% | **missed** | 436% | 1 |

## Daily, last 7 service dates

| Service date | Objective | Actual | Result | Good / total |
| --- | --- | --- | --- | --- |
| 2026-11-28 (partial) | Capture completeness | 54.57% | **missed** | 388 / 711 |
| 2026-11-28 (partial) | Gold freshness (at most 90 min old) | 100.00% | met | 12 / 12 |
| 2026-11-28 (partial) | Transform success | 100.00% | met | 12 / 12 |
| 2026-11-28 (partial) | Arrival coverage | 82.15% | met | 15,352 / 18,688 |
| 2026-11-27 | Capture completeness | 37.64% | **missed** | 542 / 1,440 |
| 2026-11-27 | Gold freshness (at most 90 min old) | 100.00% | met | 24 / 24 |
| 2026-11-27 | Transform success | 100.00% | met | 24 / 24 |
| 2026-11-27 | Arrival coverage | 76.67% | **missed** | 34,508 / 45,011 |
| 2026-11-26 | Capture completeness | 58.82% | **missed** | 847 / 1,440 |
| 2026-11-26 | Gold freshness (at most 90 min old) | 100.00% | met | 24 / 24 |
| 2026-11-26 | Transform success | 95.83% | **missed** | 23 / 24 |
| 2026-11-26 | Arrival coverage | 82.83% | met | 29,591 / 35,726 |
| 2026-11-25 | Capture completeness | 100.00% | met | 1,440 / 1,440 |
| 2026-11-25 | Gold freshness (at most 90 min old) | 100.00% | met | 24 / 24 |
| 2026-11-25 | Transform success | 100.00% | met | 24 / 24 |
| 2026-11-25 | Arrival coverage | 74.85% | **missed** | 37,263 / 49,785 |
| 2026-11-24 | Capture completeness | 100.00% | met | 1,440 / 1,440 |
| 2026-11-24 | Gold freshness (at most 90 min old) | 100.00% | met | 15 / 15 |
| 2026-11-24 | Transform success | 93.75% | **missed** | 15 / 16 |
| 2026-11-24 | Arrival coverage | 78.44% | **missed** | 39,049 / 49,785 |
| 2026-11-23 | Capture completeness | 100.00% | met | 1,440 / 1,440 |
| 2026-11-23 | Arrival coverage | 72.75% | **missed** | 36,221 / 49,785 |
| 2026-11-22 | Capture completeness | 99.89% | met | 924 / 925 |
| 2026-11-22 | Arrival coverage | 78.95% | **missed** | 15,194 / 19,244 |
| 2026-11-22 | Arrival coverage under a service disruption (separate) | 12.87% | **missed** | 401 / 3,115 |

The latest service date is still in progress and counts events up to the last complete half hour. The disrupted-lines row does not count toward the arrival coverage objective.
