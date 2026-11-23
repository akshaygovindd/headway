# Spike: arrival-reconstruction sampling (Release 2, Phase 2.0)

Question: can arrivals be reconstructed well enough from one vehicle capture per minute, or does
vehicle sampling need to go to 30 s or 15 s? Script: `scripts/spike_arrivals.py` (DuckDB + pyarrow,
local, read-only). Raw results: `.local/spike/results.json` and `poll.json` (not committed).

## Data and deviation from the brief

The brief asked for 2 full service days. The bucket does not have them: curated data starts at
2026-11-22T16:35Z (11:35 local) and the run was on 2026-11-23 (last capture 14:40 local). The spike
used **every capture available, about 27 hours (1,627 vehicle captures)**, which is a partial
Sunday plus a partial Monday. Re-run the script when two full service days exist
(`uv run python scripts/spike_arrivals.py --data-dir <dir>`); the shares below are likely to move a
little, the conclusion is unlikely to.

Scope: subway lines only, de-duplicated vehicle observations (96,427 after dropping stale
positions) timed by feed `event_ts`. Trips whose `trip_id` is not in the static trips table
(`ADDED-*` trips, 1,369 of 3,961 trip/vehicle groups) have no scheduled stops or shape and are
**excluded**, so these numbers describe scheduled trips only. Static feed version: Fall 2026
version D. Stop and vehicle positions are projected onto the trip's shape; a STOPPED_AT capture is
pinned to its stop.

## Method

- **Observed interval:** last capture before the first STOPPED_AT at a stop, up to that STOPPED_AT.
  Arrival estimate is the midpoint; confidence high when the interval is 30 s or less.
- **Bracketed:** no STOPPED_AT at the stop; interpolate by distance along the shape between the
  last capture before and the first after. Medium when that gap is 120 s or less, otherwise low.
- **Missing:** no capture before or after. A STOPPED_AT with no earlier capture also counts as
  missing (no interval can be formed).
- **Observable trip-stop:** a scheduled stop between the first and last observed position of a
  trip. "Observed wide" below is an observed interval wider than 30 s.
- **Hold-out:** for every trip-stop with an observed interval, drop all of its STOPPED_AT captures,
  reconstruct by bracketing, and compare with the observed midpoint. The reference is itself
  uncertain by up to half the interval width (median 51-59 s, so about 26-30 s either side), so
  these errors are against a midpoint, not against the true arrival.

## 1. Shares per line (observable trip-stops)

| Line | Trip-stops | Observed high | Observed wide | Bracketed medium | Bracketed low | Missing |
|---|---:|---:|---:|---:|---:|---:|
| Red | 2,609 | 18.4% | 64.5% | 12.4% | 0.3% | 4.4% |
| Orange | 6,347 | 15.3% | 58.4% | 19.5% | 1.6% | 5.1% |
| Blue | 4,607 | 12.4% | 67.7% | 19.3% | 0.4% | 0.1% |
| Green-B | 5,377 | 14.2% | 59.9% | 18.1% | 3.3% | 4.4% |
| Green-C | 5,008 | 9.5% | 51.7% | 31.9% | 3.1% | 3.8% |
| Green-D | 6,155 | 14.5% | 60.4% | 17.6% | 3.3% | 4.2% |
| Green-E | 6,179 | 11.2% | 58.8% | 23.3% | 2.4% | 4.2% |
| Mattapan | 1,866 | 6.3% | 31.5% | 51.9% | 1.2% | 9.2% |
| All | 38,148 | 13.0% | 58.3% | 22.3% | 2.2% | 4.1% |

Only 13 % of trip-stops reach "high" at 1-minute sampling. Median observed-interval width is
51-59 s (p90 79-104 s), which is what one capture per minute implies.

## 2. Hold-out accuracy

| Line | n | Median abs (s) | p90 abs (s) | Median signed (s) | Within 30 s | Within 60 s |
|---|---:|---:|---:|---:|---:|---:|
| Red | 2,064 | 54.7 | 89.9 | +53.5 | 16.3% | 57.3% |
| Orange | 4,654 | 41.0 | 71.9 | +41.0 | 30.0% | 80.5% |
| Blue | 3,308 | 44.8 | 70.8 | +44.3 | 29.1% | 77.4% |
| Green-B | 3,753 | 51.6 | 97.7 | +51.6 | 20.0% | 58.5% |
| Green-C | 2,807 | 55.9 | 100.5 | +55.9 | 16.9% | 54.0% |
| Green-D | 4,581 | 52.8 | 96.5 | +52.8 | 22.2% | 58.9% |
| Green-E | 4,237 | 56.8 | 99.4 | +56.8 | 17.6% | 53.4% |
| Mattapan | 476 | 32.6 | 51.8 | +32.6 | 40.1% | 93.3% |

Bracketing is **late by almost the whole error** (signed median equals absolute median). Distance
interpolation assumes constant speed between the two captures, but a train dwells at the stop, so
the interpolated arrival lands after the real one. Part of the bias is a bracketing artefact that
a dwell-aware model could trim, but it also shows the information is missing at 1 minute.

## 3. Feed update interval (10 min, polled every 5 s from this machine, 2026-11-23 14:55 local)

| Measure | n | Min | Median | p90 | Max |
|---|---:|---:|---:|---:|---:|
| FeedHeader timestamp change (s) | 119 | 4 | **5** | 6 | 7 |
| Per-vehicle `timestamp` update, subway (s) | 2,555 | 1 | **9** | 60 | 2,454 |
| Fetch time (ms) | 120 | 66 | 125 | 230 | 358 |

The header changed on every one of the 120 polls (no unchanged polls), so the feed republishes
about every 5 s, which is the poll grid. 107 subway vehicles; the median vehicle reports every
9 s, but the p90 of 60 s and the 2,454 s maximum are vehicles that went quiet (layover, stale).
One 10-minute sample in the weekday afternoon: indicative, not a distribution over a day.

## Conclusion

1. At 1-minute sampling only 13 % of trip-stops get a high-confidence arrival and 58 % have an
   observed interval wider than 30 s; 4 % are missing.
2. Hold-out median error is 41-57 s on every line except Mattapan (33 s), above the 30 s target;
   p90 is 70-100 s and only 16-30 % land within 30 s (54-81 % within 60 s).
3. The feed republishes every 5 s and the median vehicle updates every 9 s, so a 1-minute poll
   throws away most of the available updates; the limit is our sampling, not the source.
4. Sampling at 15 s would give median intervals of roughly 13 s (an estimate by scaling, not
   measured; the data cannot be subsampled upwards) and put most trip-stops in "high".
5. Recommendation: add 15 s vehicle sampling (vehicle feed only); 30 s is the cheaper fallback.
   See ADR-014 (proposed).
