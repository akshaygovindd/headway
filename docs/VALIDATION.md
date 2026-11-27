# Validation of reconstructed arrivals against MBTA LAMP

`fct_reconstructed_arrivals` (see [METRICS](METRICS.md)) was hand-checked on 30 trip stops
([MEASUREMENTS](MEASUREMENTS.md#arrival-reconstruction)). This page compares it at scale with the
arrival times MBTA itself publishes, describes the correction of bracketed arrivals that the first
comparison led to, and reports its effect on days it was not fitted on. Re-run with `make validate`;
it needs a download and a local build of the facts, so it is not part of `make check` or CI.

**Read this first.** MBTA's numbers are derived from the same realtime vehicle feed as ours. This is
a cross-check by an independent implementation, not a comparison with ground truth (AVL, fare gate
or a person on the platform). Where both read the same feed message they agree by construction, and
the tables below show that they do. The new information is in three places: what the match rate says
about coverage, what the bracketed stops say about interpolation, and what the definitions imply
for how to read "error". The dwell correction below was built **without any LAMP data in the model**:
LAMP is only the yardstick it is scored against.

## Source and licence

- **Dataset:** LAMP "subway on-time performance", daily Parquet, one row per `trip_id` x `stop_id`
  x `service_date` of revenue service. Files are at
  `https://performancedata.mbta.com/lamp/subway-on-time-performance-v1/YYYY-MM-DD-subway-on-time-performance-v1.parquet`
  and listed in `index.csv` beside them. Schema: the "Subway Performance Data" section of the
  [LAMP data dictionary](https://github.com/mbta/lamp/blob/main/Data_Dictionary.md).
- **Coverage:** the index lists every day from 2026-06-23 to 2026-11-26. This run uses 2026-11-22
  to 2026-11-26. The 26 Nov file was published on the evening of 26 Nov (index timestamp
  2026-11-27 02:57 UTC) and ends before our newest rows; those rows are left out (see Method). SHA-256
  of every file used is in `.local/validation/lamp/manifest.json`.
- **How MBTA derives the times (data dictionary):** `stop_timestamp` is the earliest `STOPPED_AT`
  VehiclePosition timestamp for the trip-stop pair, and falls back to the last StopTimeUpdate arrival
  when there is no VehiclePosition. `move_timestamp` is the earliest `IN_TRANSIT_TO` or
  `INCOMING_AT`. The dictionary does not state the timestamp unit; the values are POSIX seconds
  (GTFS-realtime), which matches our UTC `arrival_ts` to the second in the tables below.
- **Licence:** the MBTA developer page says use of MBTA data is governed by the
  [MassDOT Developers License Agreement](https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf)
  (read in full on 2026-11-26). Section 3.1 grants "non-exclusive, limited, and revocable rights to
  use, reproduce, and redistribute the Data". Section 4.1 requires that MassDOT is clearly
  acknowledged as the provider, that the data is not misrepresented, that ownership is not claimed,
  and that MassDOT or MBTA logos and trademarks are not used. Section 6 says the data is provided
  "as is" and may be inaccurate. The licence allows redistribution, but this repository commits no
  LAMP rows: the files stay in `.local/validation/` (git-ignored) and are downloaded by the script.
  **Data provided by the Massachusetts Department of Transportation (MassDOT) and the MBTA.** This
  project is not affiliated with MassDOT or the MBTA.
- The only network access is a plain HTTPS download of the files above; no dashboard or page is
  scraped.

## Method

1. **Our side.** `fct_reconstructed_arrivals` from a fresh read-only copy of the production curated
   data (`aws s3 sync` of `curated/`, list and get only), rebuilt locally with `HEADWAY_ENABLE_R2`
   on 2026-11-26 at about 22:50 local (`.local/prod_test.duckdb`). Rows are one per trip instance x
   run (segment) x scheduled stop; `arrival_ts` is null for method `missing`. The "before" figures
   are the same data built with an empty `reconstruction_dwell` seed, which the unit tests show is
   the previous behaviour exactly; observed arrivals are identical in both builds (0 rows differ).
2. **Their side.** Every LAMP row of those days. `line` is `route_id` (Green is split by branch,
   as in ours); `service_date` is the integer `yyyymmdd`; `stop_id` is the platform id in both;
   `direction_id` is 0/1 in both.
3. **Pass 1, trip id.** Join on service date, `trip_id`, `direction_id` and `stop_id`. Where a trip
   id was reused (several runs of ours, or several LAMP rows) the pairs are assigned one to one,
   smallest time difference first.
4. **Pass 2, nearest scheduled trip.** Rows left over from pass 1 are paired on service date, line,
   direction and stop when our `scheduled_arrival_ts` and LAMP's `scheduled_arrival_time` are within
   120 s, one to one, smallest schedule gap first.
5. **Plausibility gate.** A pair whose actual times differ by more than 600 s is not accepted in
   either pass: it is the same trip id on a different run, or an `ADDED` trip that inherited the
   scheduled time of the trip it replaced (without the gate pass 2 paired our rows with such trips
   and produced differences of up to 14 hours). Rejected candidates are counted in the outcome
   tables.
6. **LAMP's last timestamp.** The last day's file is partial when read, and our data runs later; our
   rows after LAMP's latest `stop_timestamp` are left out and counted (1,158 in this run).
7. **Statistics.** Difference = ours minus LAMP, in seconds, so a negative bias means we place the
   arrival earlier. Only rows with an `arrival_ts` are scored. The match rate is the share of those
   rows that have a LAMP counterpart. `within 30 s` is the share of matched rows whose absolute
   difference is at most 30 s.
8. **Sampling period** is taken from the arrival time: before 2026-11-26 18:00 UTC is "1 min"
   (one vehicle poll a minute), from then on "15 s" (four polls a minute). The 15 s period is about
   four hours of one evening, so it is confounded with time of day and with a single day.
9. **Fit window and held-out days.** The dwell table was fitted on service dates 2026-11-22 to
   2026-11-24 only, from our own data. 2026-11-25 and 2026-11-26 played no part in the fit and are
   reported as held out, next to the in-sample days.

## What the definitions imply

For an `observed` stop our `arrival_ts` is the midpoint of the interval from the last capture before
the first `STOPPED_AT` to that `STOPPED_AT` capture, and `interval_width_s` is the interval's width.
LAMP's `stop_timestamp` is the first `STOPPED_AT` itself, which is the end of that interval. So for
an observed stop LAMP minus ours is half the interval width, by construction, and "error against
LAMP" for observed stops measures how wide our interval is, not how wrong the midpoint is. The
true arrival lies inside the interval; LAMP's time is its upper bound. The dwell correction does not
touch observed stops.

## The dwell correction for bracketed arrivals

**Problem (first comparison).** A bracketed stop is one where no capture saw the vehicle
`STOPPED_AT`: the polls before and after straddled the whole dwell. Interpolating by distance
between them assumed constant speed through the stop, so the stop landed inside the dwell. If the
vehicle moves at constant speed except for a dwell D at the stop, interpolation lands exactly
`along x D` after the true arrival, where `along` is how far the stop lies between the two
anchors (0 to 1). Before the fix bracketed arrivals were a median 13 s late against LAMP, and the
bias followed LAMP's dwell (+2 s for dwells up to 15 s, +9 s for 16-30 s, +20 s for 31-60 s).

**What the model does now** (`fct_reconstructed_arrivals`, seed `reconstruction_dwell`): the
interpolated time is moved earlier by `along x E[dwell of a missed visit]`, limited to the time
between the anchors, never before the anchor preceding the stop, and the bracketed stops of a run
stay in order. Observed arrivals, confidence, `known_at`, `bracket_gap_s` and the schema are
unchanged; confidence counts are identical before and after, and no run has a monotonic violation.
Bracketed arrivals moved by a median of 16 s (largest 46 s).

**Estimating the dwell without LAMP.** The naive estimate, the median dwell of observed stops, is
biased twice: at one poll a minute a visit is seen with probability about `dwell / spacing`, so
observed stops over-represent long dwells, and a single sighting (127,180 of about 133,000
visits) carries no dwell information, so the estimate is mostly the poll spacing (61 s, against 36 s
for the stops that are bracketed). Applied as is, it over-corrects (median bias -17 s). The fit
(`scripts/fit_reconstruction_dwell.py`, which reads only our own `int_vehicle_stop_events` and
`fct_reconstructed_arrivals`) instead uses the split of visits by number of sightings k:

1. Dwell per line is Gamma(shape, mean); a visit with dwell D is seen `floor(D / T)` or one more
   times, T being the spacing of the run's observations (classes of 15, 30, 60 and 120 s).
2. Per line, shape and mean are chosen so the predicted shares of k = 0 (bracketed) and k >= 2 match
   the data, mixing the runs' spacing classes.
3. Per line and stop, and per line, stop and hour bucket (night, am, mid, pm, eve by the local hour
   of the scheduled arrival), the shape is kept and the mean is fitted to the share of k = 0, when
   there are at least 100 and 50 visits (and the share can be matched inside the grid).
4. For each cell and spacing class the table stores `E[D | missed] = E[D (1 - D/T)+] / E[(1 - D/T)+]`,
   the expected dwell of a visit that a poll spacing T would not see.

The dwell distribution does not depend on the polling rate, so the table stays right after the
switch to 15-second sampling: only the spacing class of the run changes the column the model reads
(in the 15-second period most runs fall in class 30, because vehicles report about every 30 s). The
model reads the most specific cell that exists (stop and hour, then stop, then line); a line that is
not in the table gets no correction. The fitted line means are 42 to 52 s (Mattapan 26 s) and the
expected dwell of a missed visit at 60 s spacing is 31 to 39 s (Mattapan 16 s).

**The shape is a prior, not a fit.** Only 0.1 to 7 % of visits have two or more sightings (about
1 % or less on Blue, Orange and Red), so the shape is barely identified and the fit slides to a near-deterministic dwell if
allowed. It is bounded above at 12 (a coefficient of variation of 0.29). The sensitivity table
shows the held-out result hardly depends on that bound.

### Before and after

All figures are "before → after", ours minus LAMP, seconds. Observed stops are unchanged, so the
rows that mix methods move only through their bracketed share.

Bracketed stops, fit days against held-out days (the held-out days play no part in the fit):

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| fit (22-24 Nov) | 24239 | 22516 | 15.7 → 6.6 | 37.4 → 27.2 | 13.6 → -3.3 | 81.4 → 90.9 | 98.6 → 98.1 |
| held out (25-26 Nov) | 14954 | 13944 | 15.3 → 7.0 | 36.7 → 31.0 | 12.4 → -4.2 | 82.6 → 89.7 | 98.3 → 97.6 |

By fit window and sampling period:

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| fit (22-24 Nov) / 1 min | 24239 | 22516 | 15.7 → 6.6 | 37.4 → 27.2 | 13.6 → -3.3 | 81.4 → 90.9 | 98.6 → 98.1 |
| held out (25-26 Nov) / 1 min | 13922 | 13154 | 15.8 → 7.0 | 36.6 → 29.2 | 13.2 → -4.1 | 82.5 → 90.3 | 98.5 → 97.9 |
| held out (25-26 Nov) / 15 s | 1032 | 790 | 9.4 → 8.2 | 38.4 → 52.6 | 2.9 → -5.3 | 84.2 → 79.6 | 93.9 → 92.0 |

Sensitivity (bracketed stops): cells down to the line only, and a different upper bound of the
shape (each variant refitted on 22-24 Nov and scored the same way):

| variant | days | median abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|
| none (before) | fit (22-24 Nov) | 15.7 | 13.6 | 81.4 | 98.6 |
| none (before) | held out (25-26 Nov) | 15.3 | 12.4 | 82.6 | 98.3 |
| line level only | fit (22-24 Nov) | 7.6 | -4.7 | 89.4 | 98.0 |
| line level only | held out (25-26 Nov) | 7.9 | -5.2 | 88.2 | 97.4 |
| line, stop, hour (chosen, shape <= 12) | fit (22-24 Nov) | 6.6 | -3.3 | 90.9 | 98.1 |
| line, stop, hour (chosen, shape <= 12) | held out (25-26 Nov) | 7.0 | -4.2 | 89.7 | 97.6 |
| line, stop, hour, shape <= 6 | fit (22-24 Nov) | 6.3 | -2.1 | 91.0 | 98.2 |
| line, stop, hour, shape <= 6 | held out (25-26 Nov) | 6.5 | -2.8 | 89.9 | 97.7 |
| line, stop, hour, shape <= 40 | fit (22-24 Nov) | 7.4 | -4.6 | 90.7 | 98.1 |
| line, stop, hour, shape <= 40 | held out (25-26 Nov) | 7.9 | -5.3 | 89.5 | 97.5 |

Bracketed stops by confidence, line and gap between the anchors:

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| medium | 36319 | 33921 | 14.9 → 6.6 | 35.6 → 26.9 | 12.5 → -3.5 | 83.6 → 91.0 | 98.9 → 98.4 |
| low | 2874 | 2539 | 27.2 → 9.2 | 49.3 → 50.0 | 23.6 → -6.0 | 58.1 → 82.4 | 93.3 → 92.0 |

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| Blue | 3331 | 3319 | 16.7 → 4.9 | 34.0 → 11.1 | 16.5 → -4.2 | 83.5 → 99.6 | 99.9 → 99.8 |
| Green-B | 4556 | 4499 | 19.3 → 7.5 | 43.0 → 38.4 | 18.4 → -1.2 | 73.6 → 87.2 | 97.8 → 98.4 |
| Green-C | 6141 | 4971 | 15.4 → 8.3 | 39.0 → 34.0 | 10.3 → -3.2 | 78.9 → 88.3 | 98.4 → 98.5 |
| Green-D | 4664 | 4565 | 20.4 → 7.3 | 43.5 → 43.7 | 16.7 → -3.8 | 73.2 → 83.6 | 97.8 → 96.2 |
| Green-E | 7157 | 5941 | 17.8 → 8.3 | 40.2 → 45.9 | 11.5 → -4.7 | 76.6 → 81.0 | 97.5 → 94.8 |
| Mattapan | 3990 | 3940 | 7.4 → 4.7 | 16.3 → 14.6 | 4.6 → -1.8 | 98.8 → 99.5 | 99.9 → 99.9 |
| Orange | 6298 | 6187 | 13.4 → 6.5 | 26.6 → 18.0 | 12.8 → -6.0 | 94.2 → 98.1 | 99.7 → 99.7 |
| Red | 3056 | 3038 | 19.0 → 9.4 | 44.9 → 28.7 | 19.0 → 1.4 | 73.1 → 90.2 | 96.6 → 96.6 |

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| <=30 | 1681 | 1340 | 9.6 → 10.6 | 50.6 → 48.0 | 1.1 → -5.6 | 82.8 → 82.5 | 93.1 → 93.3 |
| 31-60 | 14141 | 13130 | 12.8 → 6.0 | 33.7 → 24.8 | 11.3 → -2.7 | 86.0 → 91.2 | 99.2 → 99.0 |
| 61-120 | 20497 | 19451 | 16.4 → 6.8 | 36.1 → 26.5 | 14.1 → -4.0 | 82.1 → 91.5 | 99.0 → 98.3 |
| 121-180 | 2137 | 2058 | 26.5 → 7.9 | 44.6 → 37.8 | 24.5 → -5.2 | 62.0 → 88.4 | 97.3 → 96.2 |
| 181-300 | 406 | 346 | 32.5 → 19.3 | 77.5 → 85.9 | 15.8 → -13.1 | 44.8 → 66.8 | 85.0 → 82.4 |
| >300 | 331 | 135 | 57.8 → 56.0 | 264.7 → 279.8 | -7.0 → -36.0 | 34.1 → 31.9 | 52.6 → 53.3 |

By line and fit window:

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| Blue / fit (22-24 Nov) | 2297 | 2288 | 16.6 → 4.8 | 34.2 → 11.0 | 16.4 → -4.1 | 83.0 → 99.7 | 99.9 → 99.9 |
| Blue / held out (25-26 Nov) | 1034 | 1031 | 17.0 → 5.1 | 32.9 → 11.4 | 16.7 → -4.6 | 84.8 → 99.5 | 100.0 → 99.7 |
| Green-B / fit (22-24 Nov) | 2643 | 2604 | 20.9 → 7.4 | 44.5 → 40.9 | 20.0 → -0.2 | 71.2 → 86.6 | 97.7 → 98.4 |
| Green-B / held out (25-26 Nov) | 1913 | 1895 | 17.7 → 7.7 | 40.6 → 36.2 | 16.2 → -2.5 | 76.9 → 87.9 | 98.0 → 98.5 |
| Green-C / fit (22-24 Nov) | 3782 | 3079 | 15.4 → 8.2 | 38.9 → 31.9 | 10.6 → -2.8 | 78.6 → 89.2 | 98.9 → 99.0 |
| Green-C / held out (25-26 Nov) | 2359 | 1892 | 15.3 → 8.3 | 39.2 → 36.6 | 9.5 → -4.0 | 79.4 → 86.7 | 97.7 → 97.7 |
| Green-D / fit (22-24 Nov) | 2853 | 2789 | 20.4 → 6.9 | 43.2 → 41.9 | 17.5 → -3.3 | 73.2 → 84.5 | 98.1 → 96.7 |
| Green-D / held out (25-26 Nov) | 1811 | 1776 | 20.5 → 8.2 | 44.0 → 47.3 | 15.6 → -4.5 | 73.2 → 82.2 | 97.4 → 95.4 |
| Green-E / fit (22-24 Nov) | 4301 | 3555 | 18.1 → 8.0 | 41.0 → 45.8 | 12.5 → -3.8 | 75.9 → 81.3 | 97.6 → 94.7 |
| Green-E / held out (25-26 Nov) | 2856 | 2386 | 17.5 → 8.8 | 39.2 → 46.8 | 10.0 → -6.1 | 77.8 → 80.4 | 97.4 → 94.8 |
| Mattapan / fit (22-24 Nov) | 2517 | 2472 | 7.4 → 4.8 | 16.4 → 15.0 | 4.6 → -1.9 | 98.8 → 99.6 | 100.0 → 100.0 |
| Mattapan / held out (25-26 Nov) | 1473 | 1468 | 7.3 → 4.6 | 16.1 → 14.0 | 4.5 → -1.7 | 98.9 → 99.5 | 99.9 → 99.9 |
| Orange / fit (22-24 Nov) | 4030 | 3924 | 13.5 → 6.5 | 26.9 → 17.8 | 13.0 → -5.9 | 93.7 → 98.0 | 99.5 → 99.5 |
| Orange / held out (25-26 Nov) | 2268 | 2263 | 13.1 → 6.7 | 26.3 → 18.2 | 12.5 → -6.0 | 95.1 → 98.3 | 100.0 → 99.9 |
| Red / fit (22-24 Nov) | 1816 | 1805 | 19.2 → 9.6 | 45.7 → 30.4 | 19.0 → 1.9 | 71.7 → 89.8 | 97.1 → 97.1 |
| Red / held out (25-26 Nov) | 1240 | 1233 | 18.6 → 9.4 | 43.5 → 26.6 | 18.3 → 0.1 | 75.2 → 90.7 | 95.9 → 95.9 |

Diagnostic by LAMP's dwell at the stop (not an input of the model; shows what is left):

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| <=15 | 7761 | 7761 | 7.5 → 10.6 | 25.7 → 31.1 | 1.7 → -8.0 | 92.4 → 89.4 | 98.5 → 97.3 |
| 16-30 | 6546 | 6546 | 10.6 → 9.0 | 23.7 → 25.0 | 9.0 → -6.3 | 94.1 → 93.8 | 99.0 → 98.4 |
| 31-60 | 21137 | 21137 | 20.6 → 5.3 | 39.9 → 20.9 | 20.0 → -2.0 | 75.4 → 91.8 | 99.3 → 99.1 |
| >60 | 220 | 220 | 30.6 → 21.6 | 98.4 → 101.3 | 5.4 → -6.0 | 48.4 → 60.5 | 77.2 → 75.5 |

All matched rows:

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| ALL | 164585 | 161195 | 23.7 → 22.0 | 46.5 → 46.5 | -21.0 → -21.5 | 67.1 → 69.1 | 96.9 → 96.8 |

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| high | 28209 | 28076 | 10.5 → 10.5 | 14.5 → 14.5 | -10.5 → -10.5 | 99.9 → 99.9 | 99.9 → 99.9 |
| medium | 129060 | 126171 | 26.5 → 25.5 | 45.5 → 45.5 | -24.5 → -25.0 | 62.3 → 64.3 | 99.7 → 99.5 |
| low | 7316 | 6948 | 65.0 → 65.0 | 119.0 → 119.5 | -64.5 → -65.0 | 21.6 → 30.5 | 34.8 → 34.3 |

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| Blue | 18618 | 18571 | 23.5 → 22.0 | 42.0 → 42.0 | -22.0 → -22.0 | 70.9 → 73.7 | 96.5 → 96.5 |
| Green-B | 21594 | 21435 | 26.0 → 24.0 | 49.0 → 49.0 | -22.0 → -23.0 | 61.4 → 64.3 | 96.7 → 96.8 |
| Green-C | 19081 | 17831 | 26.0 → 24.0 | 50.0 → 50.0 | -22.5 → -23.4 | 61.0 → 63.6 | 95.9 → 95.9 |
| Green-D | 23110 | 22853 | 24.5 → 23.0 | 49.0 → 49.5 | -21.5 → -22.5 | 68.4 → 70.5 | 96.3 → 96.0 |
| Green-E | 25596 | 24299 | 25.1 → 24.0 | 49.0 → 50.0 | -22.5 → -23.5 | 64.2 → 65.2 | 96.1 → 95.4 |
| Mattapan | 6944 | 6849 | 11.5 → 9.2 | 30.5 → 30.5 | -5.8 → -7.9 | 89.2 → 89.6 | 99.1 → 99.1 |
| Orange | 28805 | 28607 | 21.5 → 20.5 | 41.5 → 41.5 | -20.5 → -20.5 | 68.4 → 69.2 | 99.1 → 99.1 |
| Red | 20837 | 20750 | 24.0 → 22.5 | 46.3 → 46.0 | -21.5 → -21.5 | 68.0 → 70.5 | 96.3 → 96.3 |

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| 1 min | 149450 | 146369 | 24.7 → 23.0 | 46.5 → 46.8 | -22.0 → -22.5 | 65.4 → 67.6 | 96.8 → 96.7 |
| 15 s | 15135 | 14826 | 12.5 → 12.5 | 39.7 → 40.5 | -12.0 → -12.5 | 83.8 → 83.6 | 97.9 → 97.8 |

By method and confidence:

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| bracketed / low | 2874 | 2539 | 27.2 → 9.2 | 49.3 → 50.0 | 23.6 → -6.0 | 58.1 → 82.4 | 93.3 → 92.0 |
| bracketed / medium | 36319 | 33921 | 14.9 → 6.6 | 35.6 → 26.9 | 12.5 → -3.5 | 83.6 → 91.0 | 98.9 → 98.4 |
| observed / high | 28209 | 28076 | 10.5 → 10.5 | 14.5 → 14.5 | -10.5 → -10.5 | 99.9 → 99.9 | 99.9 → 99.9 |
| observed / low | 4442 | 4409 | 75.0 → 75.0 | 131.5 → 131.5 | -75.0 → -75.0 | 0.6 → 0.6 | 1.1 → 1.1 |
| observed / medium | 92741 | 92250 | 29.0 → 29.0 | 47.0 → 47.0 | -29.0 → -29.0 | 54.5 → 54.5 | 99.9 → 99.9 |

By line and confidence:

| group | rows | matched | median abs s | p90 abs s | median bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|
| Blue / high | 3309 | 3301 | 11.0 → 11.0 | 14.5 → 14.5 | -11.0 → -11.0 | 100.0 → 100.0 | 100.0 → 100.0 |
| Blue / low | 706 | 699 | 94.0 → 94.0 | 145.8 → 145.8 | -94.0 → -94.0 | 2.3 → 6.4 | 7.7 → 7.3 |
| Blue / medium | 14603 | 14571 | 25.5 → 24.5 | 41.0 → 41.0 | -24.0 → -24.5 | 67.6 → 71.0 | 100.0 → 100.0 |
| Green-B / high | 4020 | 4002 | 9.0 → 9.0 | 14.0 → 14.0 | -9.0 → -9.0 | 100.0 → 100.0 | 100.0 → 100.0 |
| Green-B / low | 1098 | 1067 | 62.0 → 62.0 | 95.2 → 95.2 | -62.0 → -62.0 | 27.3 → 37.7 | 41.8 → 41.5 |
| Green-B / medium | 16476 | 16366 | 29.0 → 28.0 | 48.5 → 48.0 | -27.0 → -27.0 | 54.2 → 57.3 | 99.5 → 99.6 |
| Green-C / high | 2550 | 2539 | 8.5 → 8.5 | 14.0 → 14.0 | -8.5 → -8.5 | 99.8 → 99.8 | 99.8 → 99.8 |
| Green-C / low | 1222 | 1148 | 62.0 → 62.0 | 101.0 → 101.0 | -62.0 → -62.0 | 27.3 → 38.4 | 42.9 → 42.9 |
| Green-C / medium | 15309 | 14144 | 28.0 → 27.0 | 48.5 → 48.5 | -26.0 → -26.5 | 56.8 → 59.1 | 99.4 → 99.5 |
| Green-D / high | 4202 | 4179 | 10.5 → 10.5 | 14.5 → 14.5 | -10.5 → -10.5 | 99.8 → 99.8 | 99.8 → 99.8 |
| Green-D / low | 1384 | 1328 | 62.5 → 62.5 | 105.0 → 104.6 | -62.3 → -62.5 | 25.8 → 38.3 | 42.6 → 42.1 |
| Green-D / medium | 17524 | 17346 | 27.0 → 26.0 | 48.0 → 48.5 | -25.0 → -25.5 | 64.1 → 65.9 | 99.6 → 99.2 |
| Green-E / high | 3728 | 3709 | 9.5 → 9.5 | 14.0 → 14.0 | -9.5 → -9.5 | 99.9 → 99.9 | 99.9 → 99.9 |
| Green-E / low | 1574 | 1495 | 62.5 → 63.0 | 105.5 → 107.0 | -62.5 → -63.0 | 27.0 → 35.8 | 42.0 → 41.1 |
| Green-E / medium | 20294 | 19095 | 27.1 → 26.5 | 47.5 → 48.0 | -25.5 → -26.5 | 60.1 → 60.8 | 99.6 → 98.8 |
| Mattapan / high | 770 | 751 | 9.0 → 9.0 | 14.5 → 14.5 | -9.0 → -9.0 | 99.7 → 99.7 | 99.7 → 99.7 |
| Mattapan / low | 151 | 129 | 17.1 → 21.3 | 167.5 → 167.5 | -15.9 → -21.3 | 53.5 → 52.7 | 55.0 → 55.0 |
| Mattapan / medium | 6023 | 5969 | 12.2 → 9.2 | 31.0 → 30.5 | -3.3 → -7.2 | 88.7 → 89.1 | 99.9 → 99.9 |
| Orange / high | 5506 | 5489 | 12.5 → 12.5 | 14.5 → 14.5 | -12.5 → -12.5 | 100.0 → 100.0 | 100.0 → 100.0 |
| Orange / low | 369 | 282 | 76.3 → 77.3 | 200.8 → 204.9 | -76.3 → -77.3 | 7.8 → 4.6 | 11.7 → 11.0 |
| Orange / medium | 22930 | 22836 | 25.0 → 24.0 | 42.5 → 42.5 | -23.5 → -24.0 | 61.6 → 62.6 | 100.0 → 100.0 |
| Red / high | 4124 | 4106 | 11.5 → 11.5 | 14.5 → 14.5 | -11.5 → -11.5 | 99.9 → 99.9 | 100.0 → 100.0 |
| Red / low | 812 | 800 | 71.0 → 71.0 | 132.6 → 132.6 | -71.0 → -71.0 | 6.0 → 13.5 | 15.8 → 15.4 |
| Red / medium | 15901 | 15844 | 27.0 → 26.0 | 45.0 → 44.5 | -25.5 → -25.5 | 62.9 → 65.7 | 99.4 → 99.4 |

## Results (after the correction)

Service dates 2026-11-22 to 2026-11-26. Full output, including every line x period cell, is written
by `make validate` to `.local/validation/results.md`.

### Coverage

Our rows by outcome:

| outcome | rows |
|---|---|
| matched | 161195 |
| no arrival_ts (missing), LAMP has a time | 4152 |
| stop not in LAMP trip | 3058 |
| no arrival_ts (missing), not in LAMP either | 2974 |
| same trip and stop, more than 10 min apart (reused id?) | 227 |
| LAMP row has no stop_timestamp | 57 |
| trip not in LAMP | 33 |
| other (direction or duplicate) | 15 |

LAMP rows by outcome:

| outcome | rows |
|---|---|
| matched | 161195 |
| unscheduled trip id (ADDED, NONREV, ...) | 20871 |
| trip in ours, stop not reconstructed | 10077 |
| scheduled trip not in our facts | 7258 |
| ours has no arrival_ts (missing) | 4152 |
| no stop_timestamp | 3998 |
| same trip and stop, more than 10 min apart (reused id?) | 169 |
| other (our row is missing or duplicate) | 71 |

- **Why unmatched, our side.** "stop not in LAMP trip" is mostly bracketed Green-C and Green-E
  stops where LAMP has no row for the stop (presumably LAMP saw no position or stop-time update
  there; not checked). "no arrival_ts, LAMP has a time" are 4,152 stops we mark `missing` that LAMP
  has a time for: our gap, not LAMP's.
- **Why unmatched, LAMP side.** 20,871 rows are unscheduled trips (`ADDED-`, `NONREV-`), which we
  do not reconstruct by design. All 7,258 "scheduled trip not in our facts" rows are 22 Nov, when
  our capture started at about 11:30 local; they are not a model gap. 10,077 are stops of trips we
  have but did not reconstruct (outside the observed stretch, or no usable observation).
- **Match mechanism.** Of the matched pairs, all but 52 matched by trip id (pass 1). 74 of our rows
  had more than one LAMP candidate (reused trip ids).

### Ours minus LAMP, all matched rows

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 164585 | 161195 | 97.9 | 22.0 | 46.5 | -21.5 | -22.8 | 69.1 | 96.8 |

By line:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| Blue | 18618 | 18571 | 99.7 | 22.0 | 42.0 | -22.0 | -24.0 | 73.7 | 96.5 |
| Green-B | 21594 | 21435 | 99.3 | 24.0 | 49.0 | -23.0 | -22.9 | 64.3 | 96.8 |
| Green-C | 19081 | 17831 | 93.4 | 24.0 | 50.0 | -23.4 | -23.0 | 63.6 | 95.9 |
| Green-D | 23110 | 22853 | 98.9 | 23.0 | 49.5 | -22.5 | -23.1 | 70.5 | 96.0 |
| Green-E | 25596 | 24299 | 94.9 | 24.0 | 50.0 | -23.5 | -24.7 | 65.2 | 95.4 |
| Mattapan | 6944 | 6849 | 98.6 | 9.2 | 30.5 | -7.9 | -11.5 | 89.6 | 99.1 |
| Orange | 28805 | 28607 | 99.3 | 20.5 | 41.5 | -20.5 | -22.6 | 69.2 | 99.1 |
| Red | 20837 | 20750 | 99.6 | 22.5 | 46.0 | -21.5 | -22.8 | 70.5 | 96.3 |

By confidence:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| high | 28209 | 28076 | 99.5 | 10.5 | 14.5 | -10.5 | -9.7 | 99.9 | 99.9 |
| medium | 129060 | 126171 | 97.8 | 25.5 | 45.5 | -25.0 | -23.5 | 64.3 | 99.5 |
| low | 7316 | 6948 | 95.0 | 65.0 | 119.5 | -65.0 | -62.6 | 30.5 | 34.3 |

By sampling period:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| 1 min | 149450 | 146369 | 97.9 | 23.0 | 46.8 | -22.5 | -23.3 | 67.6 | 96.7 |
| 15 s | 15135 | 14826 | 98.0 | 12.5 | 40.5 | -12.5 | -17.1 | 83.6 | 97.8 |

By confidence and sampling period:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| high / 1 min | 19742 | 19640 | 99.5 | 11.5 | 14.5 | -11.5 | -10.3 | 99.9 | 99.9 |
| high / 15 s | 8467 | 8436 | 99.6 | 8.0 | 13.5 | -8.0 | -8.3 | 99.9 | 100.0 |
| low / 1 min | 6991 | 6654 | 95.2 | 64.5 | 118.8 | -64.5 | -61.6 | 31.7 | 35.5 |
| low / 15 s | 325 | 294 | 90.5 | 74.3 | 133.9 | -73.8 | -86.8 | 3.4 | 6.8 |
| medium / 1 min | 122717 | 120075 | 97.8 | 25.5 | 45.5 | -25.0 | -23.4 | 64.3 | 99.5 |
| medium / 15 s | 6343 | 6096 | 96.1 | 23.5 | 48.5 | -23.5 | -26.0 | 64.8 | 99.1 |

Observed rows by interval width (the difference is half the width, as above):

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| <=15 | 8354 | 8309 | 99.5 | 5.5 | 7.5 | -5.5 | -4.9 | 99.9 | 99.9 |
| 16-30 | 19855 | 19767 | 99.6 | 12.5 | 14.5 | -12.0 | -11.7 | 99.9 | 99.9 |
| 31-60 | 50114 | 49885 | 99.5 | 23.5 | 29.0 | -23.5 | -22.6 | 99.9 | 99.9 |
| 61-120 | 42627 | 42365 | 99.4 | 39.5 | 51.5 | -39.5 | -40.3 | 1.0 | 100.0 |
| >120 | 4442 | 4409 | 99.3 | 75.0 | 131.5 | -75.0 | -90.6 | 0.6 | 1.1 |

### Observed rows shifted to the end of their interval

Adding half the interval width to our time puts it on the same message LAMP uses. If both read the
same feed in the same way this is zero. The correction does not touch these rows.

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 5 s % | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL | 125392 | 124735 | 99.5 | 0.0 | 0.0 | 0.0 | 0.5 | 98.7 | 99.8 | 99.9 |

By line:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 5 s % | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|---|
| Blue | 15287 | 15252 | 99.8 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 100.0 | 100.0 |
| Green-B | 17038 | 16936 | 99.4 | 0.0 | 0.0 | 0.0 | 0.2 | 99.3 | 99.8 | 100.0 |
| Green-C | 12940 | 12860 | 99.4 | 0.0 | 0.0 | 0.0 | 0.8 | 99.6 | 99.8 | 99.8 |
| Green-D | 18446 | 18288 | 99.1 | 0.0 | 0.0 | 0.0 | 1.0 | 97.7 | 99.8 | 99.9 |
| Green-E | 18439 | 18358 | 99.6 | 0.0 | 0.0 | 0.0 | 0.6 | 97.4 | 99.8 | 99.9 |
| Mattapan | 2954 | 2909 | 98.5 | 0.0 | 0.0 | 0.0 | 0.7 | 99.8 | 99.8 | 99.8 |
| Orange | 22507 | 22420 | 99.6 | 0.0 | 0.0 | 0.0 | 0.1 | 99.5 | 99.9 | 100.0 |
| Red | 17781 | 17712 | 99.6 | 0.0 | 0.0 | 0.0 | 0.5 | 97.6 | 99.5 | 99.9 |

By confidence:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 5 s % | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|---|
| high | 28209 | 28076 | 99.5 | 0.0 | 0.0 | 0.0 | 0.3 | 99.7 | 99.9 | 99.9 |
| medium | 92741 | 92250 | 99.5 | 0.0 | 0.0 | 0.0 | 0.5 | 98.4 | 99.8 | 99.9 |
| low | 4442 | 4409 | 99.3 | 0.0 | 0.0 | 0.0 | 0.7 | 98.0 | 99.2 | 99.9 |

The 1.3 % of observed rows more than 5 s from zero were not examined row by row, so no cause is
claimed for them.

## Findings

1. **Match rate 97.9 %** of our rows with an arrival time have a LAMP counterpart, 99.5 % for
   `high`, 97.8 % for `medium`, 95.0 % for `low`. The shortfall is concentrated in bracketed
   stops on Green-C and Green-E that LAMP has no row for.
2. **Observed stops agree with LAMP to the second** once the definitions are aligned (median
   difference 0 s, 98.7 % within 5 s, 99.8 % within 30 s). This confirms the reconstruction reads
   the same `STOPPED_AT` as LAMP for the same trip and stop, and that trip, stop, direction and
   service-date matching and the UTC conversion are consistent. It does not test the midpoint,
   since LAMP reports the interval's end.
3. **The -21 s median bias is definitional, not a bug.** Ours is the interval midpoint, LAMP's is
   the interval end: bias = -(interval width / 2). At 15 s sampling intervals are narrower and the
   median bias is -12.5 s. Treat MBTA's published time as the latest the vehicle could have arrived.
4. **Bracketed arrivals were systematically late and the dwell correction removes most of it,
   including on days it was not fitted on.** Held out (25-26 Nov): median bias +12.4 to -4.2 s,
   median absolute difference 15.3 to 7.0 s, within 30 s 82.6 to 89.7 %. In sample (22-24 Nov):
   +13.6 to -3.3 s, 15.7 to 6.6 s, 81.4 to 90.9 %. The held-out figures are close to the in-sample
   ones, so the table is not overfitted to its days. The correction slightly over-shoots (a median
   of -3 to -4 s), most for stops with short dwells (bias -8 s for dwells up to 15 s) and least for
   the common 31-60 s dwell (-2 s). It is weakest on Green-D and Green-E (82.2 and 80.4 % within 30 s
   held out) and there the share within 60 s falls (Green-E 97.4 to 94.8 %, Green-D 97.4 to 95.4 %):
   the correction helps the typical stop and hurts the tail; the cause was not investigated.
5. **Stop and hour cells help a little.** Line-level cells alone give 88.2 % within 30 s held out
   against 89.7 % with the full hierarchy; the shape bound (6 to 40) moves it between 89.5 and
   89.9 %.
6. **The correction does not help in the 15-second sample.** Held out, 15-second period, 790
   bracketed stops: median bias +2.9 to -5.3 s, median absolute 9.4 to 8.2 s, but within 30 s
   falls from 84.2 to 79.6 % and within 60 s from 93.9 to 92.0 %. At 15-30 s spacing the bracketed
   stops had little bias to remove, and the table (fitted on 1-minute data and carried to other
   spacings by the dwell model, not by data) over-corrects there. This is four hours of one
   evening, so it is a flag, not a measurement; see the limitations.
7. **Overall figures move little** because 76 % of scored rows are observed stops and are unchanged:
   all matched rows go from median absolute 23.7 to 22.0 s and within 30 s from 67.1 to 69.1 %.
8. **No bug found** in matching of trip, stop, direction or service date. The 227 rows more than
   10 min from their LAMP counterpart (0.14 %) are reused trip ids where the two sides chose
   different runs, or genuine disagreements; they are listed in the outcome table, not scored.

## Limitations

- **Not ground truth.** Same feed, same vehicle reports. A vehicle that reports `STOPPED_AT` late,
  or a feed outage, affects both sides alike and cancels in the comparison. A position error is
  invisible here.
- **LAMP is also the yardstick for held-out days**, and its own time for a bracketed stop is the
  first `STOPPED_AT` in a feed we sample more coarsely. Where LAMP has no VehiclePosition it uses the
  last StopTimeUpdate arrival, a prediction; the file does not say which rows those are.
- **Observed rows are validated only for identity**, not accuracy (see "What the definitions
  imply"). The honest error bound for an observed stop is half its interval, whatever LAMP says.
- **Five days over the Thanksgiving week**, the first starting at about 11:30 local and the last
  ending in the evening: ridership, dwell and service patterns are not typical. The fit uses three
  of them, the held-out check two, and they are adjacent days, so the held-out check is evidence
  against overfitting to particular trips, not against a change of season.
- **The 15 s period** is about four hours of one evening (790 matched bracketed stops).
- **Dwell shape is assumed**, bounded at 12; the spacing carry-over to 15-30 s polling rests on the
  assumption that the dwell distribution does not change with sampling.
- **Pass 2 is rare** (52 pairs) and tuned by the 600 s gate; the gate itself is a judgment, and
  227 candidate pairs were rejected by it.
- **`stop_id` is the platform id in both datasets.** A stop renamed in static GTFS between the two
  builds would appear as unmatched.
- **Public history was not rebuilt.** Closed-day aggregates that read arrival times keep the old
  values until their metric versions are bumped (RUNBOOK, Closed-day facts).

## Do the confidence thresholds still hold?

Current: `high` = observed with interval <= 30 s, `medium` = observed <= 120 s or bracketed with
gap <= 120 s, `low` otherwise. **Nothing is changed.** Proposals, for a decision:

- **Observed `high` / `medium` / `low`: hold.** LAMP cannot tell us. The error bound is half the
  interval (at most 15 s, 60 s and over 60 s) and the thresholds describe interval width honestly.
  If the product wants "within 30 s of the true arrival", the observed `medium` band (31-120 s
  intervals) does not guarantee it; the bound is 60 s at the top of the band.
- **Bracketed `medium` (gap <= 120 s): now holds at "about 90 % within 30 s"** (91.0 % within 30 s,
  98.4 % within 60 s over the days; 90.3 % within 30 s held out at 1-minute sampling, for all
  bracketed stops). Before the correction it
  was 83.6 % and did not.
- **Bracketed `low` (gap > 120 s): the boundary may be too tight.** Gaps of 121-180 s are now 88.4 %
  within 30 s (62.0 % before), about as good as the 31-120 s bands; 181-300 s is 66.8 % and above
  300 s 31.9 %. **Proposal: raise the bracketed `medium` limit from 120 s to 180 s**, which moves
  2,137 rows (5 % of bracketed stops) from `low` to `medium`. This also changes `int_usable_arrivals`
  (it keeps high and medium), so every deviation and headway metric would gain those rows; the
  change needs its own version bump and re-measurement, not a drive-by. A limit at 240 s is not
  supported: 181-300 s is 66.8 %, and the gaps between 180 and 240 s are not shown separately.
- **Bracketed with gap <= 30 s: no change.** 82.5 % within 30 s and no better than the 31-60 s band;
  it is a small, noisy group (1,340 matched) and there is no case for a separate tier.
- **At 15-second sampling: re-measure before touching anything.** The bracketed `medium` band at 15 s
  is 79.6 % within 30 s held out, below the 1-minute figure, on four hours of data. If that
  holds on several full days, the table needs a refit on 15 s data (where the shape is also better
  identified) and the 15 s bracketed band may need its own limit.
- **Re-measure** with `make validate` after every refit or threshold change, and again once several
  full days of 15 s sampling exist; at that point observed intervals will mostly be <= 30 s and the
  `medium` band shrinks.
