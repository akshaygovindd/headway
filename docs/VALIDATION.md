# Validation of reconstructed arrivals against MBTA LAMP

`fct_reconstructed_arrivals` (see [METRICS](METRICS.md)) was hand-checked on 30 trip stops
([MEASUREMENTS](MEASUREMENTS.md#arrival-reconstruction)). This page compares it at scale with the
arrival times MBTA itself publishes. Re-run with `make validate`; it needs a download and a local
build of the facts, so it is not part of `make check` or CI.

**Read this first.** MBTA's numbers are derived from the same realtime vehicle feed as ours. This is
a cross-check by an independent implementation, not a comparison with ground truth (AVL, fare gate
or a person on the platform). Where both read the same feed message they agree by construction, and
the tables below show that they do. The new information is in three places: what the match rate says
about coverage, what the bracketed stops say about interpolation, and what the definitions imply
for how to read "error".

## Source and licence

- **Dataset:** LAMP "subway on-time performance", daily Parquet, one row per `trip_id` x `stop_id`
  x `service_date` of revenue service. Files are at
  `https://performancedata.mbta.com/lamp/subway-on-time-performance-v1/YYYY-MM-DD-subway-on-time-performance-v1.parquet`
  and listed in `index.csv` beside them. Schema: the "Subway Performance Data" section of the
  [LAMP data dictionary](https://github.com/mbta/lamp/blob/main/Data_Dictionary.md).
- **Coverage:** the index lists every day from 2026-06-23 to 2026-11-26. This run uses 2026-11-22
  to 2026-11-26. The 26 Nov file was published on the evening of 26 Nov (index timestamp
  2026-11-27 02:57 UTC); it is the day's file as of then, and a later publish may differ. SHA-256
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
   on 2026-11-26 (`.local/prod_test.duckdb`). Rows are one per trip instance x run (segment) x
   scheduled stop; `arrival_ts` is null for method `missing`.
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
6. **Statistics.** Difference = ours minus LAMP, in seconds, so a negative bias means we place the
   arrival earlier. Only rows with an `arrival_ts` are scored. The match rate is the share of those
   rows that have a LAMP counterpart. `within 30 s` is the share of matched rows whose absolute
   difference is at most 30 s.
7. **Sampling period** is taken from the arrival time: before 2026-11-26 18:00 UTC is "1 min"
   (one vehicle poll a minute), from then on "15 s" (four polls a minute). The 15 s period is about
   four hours of one evening, so it is confounded with time of day and with a single day.

## What the definitions imply

For an `observed` stop our `arrival_ts` is the midpoint of the interval from the last capture before
the first `STOPPED_AT` to that `STOPPED_AT` capture, and `interval_width_s` is the interval's width.
LAMP's `stop_timestamp` is the first `STOPPED_AT` itself, which is the end of that interval. So for
an observed stop LAMP minus ours is half the interval width, by construction, and "error against
LAMP" for observed stops measures how wide our interval is, not how wrong the midpoint is. The
true arrival lies inside the interval; LAMP's time is its upper bound.

## Results

Service dates 2026-11-22 to 2026-11-26. Full output, including every line x period cell, is written
by `make validate` to `.local/validation/results.md`.

### Coverage

Our rows by outcome:

| outcome | rows |
|---|---|
| matched | 161189 |
| no arrival_ts (missing), LAMP has a time | 4157 |
| stop not in LAMP trip | 3057 |
| no arrival_ts (missing), not in LAMP either | 2970 |
| same trip and stop, more than 10 min apart (reused id?) | 232 |
| LAMP row has no stop_timestamp | 52 |
| trip not in LAMP | 33 |
| other (direction or duplicate) | 15 |

LAMP rows by outcome:

| outcome | rows |
|---|---|
| matched | 161189 |
| unscheduled trip id (ADDED, NONREV, ...) | 20871 |
| trip in ours, stop not reconstructed | 10076 |
| scheduled trip not in our facts | 7255 |
| ours has no arrival_ts (missing) | 4157 |
| no stop_timestamp | 3998 |
| same trip and stop, more than 10 min apart (reused id?) | 174 |
| other (our row is missing or duplicate) | 71 |

- **Why unmatched, our side.** "stop not in LAMP trip" is mostly bracketed Green-C (1,155) and
  Green-E (1,185) stops where LAMP has no row for the stop (presumably LAMP saw no position or stop-time
  update there; not checked). "no arrival_ts, LAMP has a time" are 4,157 stops we mark
  `missing` that LAMP has a time for: our gap, not LAMP's.
- **Why unmatched, LAMP side.** 20,871 rows are unscheduled trips (`ADDED-`, `NONREV-`), which we
  do not reconstruct by design. All 7,255 "scheduled trip not in our facts" rows are 22 Nov, when
  our capture started at about 11:30 local; they are not a model gap. 10,076 are stops of trips we
  have but did not reconstruct (outside the observed stretch, or no usable observation).
- **Match mechanism.** Of the matched pairs, all but 52 matched by trip id (pass 1). 74 of our rows
  had more than one LAMP candidate (reused trip ids).

### Ours minus LAMP, all matched rows

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 164578 | 161189 | 97.9 | 23.7 | 46.5 | -21.0 | -19.0 | 67.1 | 96.9 |

By line:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| Blue | 18618 | 18571 | 99.7 | 23.5 | 42.0 | -22.0 | -20.1 | 70.9 | 96.5 |
| Green-B | 21591 | 21435 | 99.3 | 26.0 | 49.0 | -22.0 | -19.0 | 61.4 | 96.7 |
| Green-C | 19081 | 17829 | 93.4 | 26.0 | 50.0 | -22.5 | -19.0 | 61.0 | 95.9 |
| Green-D | 23109 | 22852 | 98.9 | 24.5 | 49.0 | -21.5 | -19.2 | 68.4 | 96.3 |
| Green-E | 25595 | 24297 | 94.9 | 25.1 | 49.0 | -22.5 | -20.3 | 64.2 | 96.1 |
| Mattapan | 6944 | 6849 | 98.6 | 11.5 | 30.5 | -5.8 | -8.0 | 89.2 | 99.1 |
| Orange | 28804 | 28607 | 99.3 | 21.5 | 41.5 | -20.5 | -18.6 | 68.4 | 99.1 |
| Red | 20836 | 20749 | 99.6 | 24.0 | 46.3 | -21.5 | -20.3 | 68.0 | 96.3 |

By confidence:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| high | 28208 | 28075 | 99.5 | 10.5 | 14.5 | -10.5 | -9.7 | 99.9 | 99.9 |
| medium | 129056 | 126166 | 97.8 | 26.5 | 45.5 | -24.5 | -19.2 | 62.3 | 99.7 |
| low | 7314 | 6948 | 95.0 | 65.0 | 119.0 | -64.5 | -53.2 | 21.6 | 34.8 |

By sampling period:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| 1 min | 149448 | 146362 | 97.9 | 24.7 | 46.5 | -22.0 | -19.2 | 65.4 | 96.8 |
| 15 s | 15130 | 14827 | 98.0 | 12.5 | 39.6 | -12.0 | -16.7 | 83.8 | 97.9 |

By method and confidence:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| bracketed / low | 2874 | 2539 | 88.3 | 27.2 | 49.3 | 23.6 | 11.9 | 58.1 | 93.3 |
| bracketed / medium | 36319 | 33916 | 93.4 | 14.9 | 35.6 | 12.5 | 12.3 | 83.6 | 98.9 |
| observed / high | 28208 | 28075 | 99.5 | 10.5 | 14.5 | -10.5 | -9.7 | 99.9 | 99.9 |
| observed / low | 4440 | 4409 | 99.3 | 75.0 | 131.5 | -75.0 | -90.6 | 0.6 | 1.1 |
| observed / medium | 92737 | 92250 | 99.5 | 29.0 | 47.0 | -29.0 | -30.7 | 54.5 | 99.9 |

By line and confidence:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| Blue / high | 3309 | 3301 | 99.8 | 11.0 | 14.5 | -11.0 | -10.5 | 100.0 | 100.0 |
| Blue / low | 706 | 699 | 99.0 | 94.0 | 145.8 | -94.0 | -95.6 | 2.3 | 7.7 |
| Blue / medium | 14603 | 14571 | 99.8 | 25.5 | 41.0 | -24.0 | -18.6 | 67.6 | 100.0 |
| Green-B / high | 4020 | 4002 | 99.6 | 9.0 | 14.0 | -9.0 | -8.6 | 100.0 | 100.0 |
| Green-B / low | 1097 | 1067 | 97.3 | 62.0 | 95.2 | -62.0 | -37.9 | 27.3 | 41.8 |
| Green-B / medium | 16474 | 16366 | 99.3 | 29.0 | 48.5 | -27.0 | -20.3 | 54.2 | 99.5 |
| Green-C / high | 2550 | 2539 | 99.6 | 8.5 | 14.0 | -8.5 | -7.8 | 99.8 | 99.8 |
| Green-C / low | 1222 | 1148 | 93.9 | 62.0 | 101.0 | -62.0 | -38.2 | 27.3 | 42.9 |
| Green-C / medium | 15309 | 14142 | 92.4 | 28.0 | 48.5 | -26.0 | -19.4 | 56.8 | 99.4 |
| Green-D / high | 4202 | 4179 | 99.5 | 10.5 | 14.5 | -10.5 | -9.0 | 99.8 | 99.8 |
| Green-D / low | 1383 | 1328 | 96.0 | 62.5 | 105.0 | -62.3 | -41.2 | 25.8 | 42.6 |
| Green-D / medium | 17524 | 17345 | 99.0 | 27.0 | 48.0 | -25.0 | -20.0 | 64.1 | 99.6 |
| Green-E / high | 3728 | 3709 | 99.5 | 9.5 | 14.0 | -9.5 | -9.0 | 99.9 | 99.9 |
| Green-E / low | 1574 | 1495 | 95.0 | 62.5 | 105.5 | -62.5 | -43.8 | 27.0 | 42.0 |
| Green-E / medium | 20293 | 19093 | 94.1 | 27.1 | 47.5 | -25.5 | -20.6 | 60.1 | 99.6 |
| Mattapan / high | 770 | 751 | 97.5 | 9.0 | 14.5 | -9.0 | -8.9 | 99.7 | 99.7 |
| Mattapan / low | 151 | 129 | 85.4 | 17.1 | 167.5 | -15.9 | -54.9 | 53.5 | 55.0 |
| Mattapan / medium | 6023 | 5969 | 99.1 | 12.2 | 31.0 | -3.3 | -6.9 | 88.7 | 99.9 |
| Orange / high | 5506 | 5489 | 99.7 | 12.5 | 14.5 | -12.5 | -11.0 | 100.0 | 100.0 |
| Orange / low | 369 | 282 | 76.4 | 76.3 | 200.8 | -76.3 | -103.1 | 7.8 | 11.7 |
| Orange / medium | 22929 | 22836 | 99.6 | 25.0 | 42.5 | -23.5 | -19.4 | 61.6 | 100.0 |
| Red / high | 4123 | 4105 | 99.6 | 11.5 | 14.5 | -11.5 | -10.9 | 99.9 | 100.0 |
| Red / low | 812 | 800 | 98.5 | 71.0 | 132.6 | -71.0 | -77.4 | 6.0 | 15.8 |
| Red / medium | 15901 | 15844 | 99.6 | 27.0 | 45.0 | -25.5 | -19.9 | 62.9 | 99.4 |

By confidence and sampling period:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| high / 1 min | 19742 | 19640 | 99.5 | 11.5 | 14.5 | -11.5 | -10.3 | 99.9 | 99.9 |
| high / 15 s | 8466 | 8435 | 99.6 | 8.0 | 13.5 | -8.0 | -8.3 | 99.9 | 100.0 |
| low / 1 min | 6991 | 6654 | 95.2 | 64.5 | 118.0 | -64.5 | -51.7 | 22.5 | 36.0 |
| low / 15 s | 323 | 294 | 91.0 | 74.3 | 133.9 | -73.8 | -85.5 | 3.4 | 7.5 |
| medium / 1 min | 122715 | 120068 | 97.8 | 26.5 | 45.5 | -25.0 | -18.9 | 62.2 | 99.7 |
| medium / 15 s | 6341 | 6098 | 96.2 | 23.5 | 47.5 | -23.5 | -24.9 | 65.4 | 99.4 |

Observed rows by interval width (the difference is half the width, as above):

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| <=15 | 8354 | 8309 | 99.5 | 5.5 | 7.5 | -5.5 | -4.9 | 99.9 | 99.9 |
| 16-30 | 19854 | 19766 | 99.6 | 12.5 | 14.5 | -12.0 | -11.7 | 99.9 | 99.9 |
| 31-60 | 50112 | 49885 | 99.5 | 23.5 | 29.0 | -23.5 | -22.6 | 99.9 | 99.9 |
| 61-120 | 42625 | 42365 | 99.4 | 39.5 | 51.5 | -39.5 | -40.3 | 1.0 | 100.0 |
| >120 | 4440 | 4409 | 99.3 | 75.0 | 131.5 | -75.0 | -90.6 | 0.6 | 1.1 |

### Observed rows shifted to the end of their interval

Adding half the interval width to our time puts it on the same message LAMP uses. If both read the
same feed in the same way this is zero.

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 5 s % | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL | 125385 | 124734 | 99.5 | 0.0 | 0.0 | 0.0 | 0.5 | 98.7 | 99.8 | 99.9 |

By line:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 5 s % | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|---|
| Blue | 15287 | 15252 | 99.8 | 0.0 | 0.0 | 0.0 | 0.0 | 100.0 | 100.0 | 100.0 |
| Green-B | 17035 | 16936 | 99.4 | 0.0 | 0.0 | 0.0 | 0.2 | 99.3 | 99.8 | 100.0 |
| Green-C | 12940 | 12860 | 99.4 | 0.0 | 0.0 | 0.0 | 0.8 | 99.6 | 99.8 | 99.8 |
| Green-D | 18444 | 18288 | 99.2 | 0.0 | 0.0 | 0.0 | 1.0 | 97.7 | 99.8 | 99.9 |
| Green-E | 18439 | 18358 | 99.6 | 0.0 | 0.0 | 0.0 | 0.6 | 97.4 | 99.8 | 99.9 |
| Mattapan | 2954 | 2909 | 98.5 | 0.0 | 0.0 | 0.0 | 0.7 | 99.8 | 99.8 | 99.8 |
| Orange | 22506 | 22420 | 99.6 | 0.0 | 0.0 | 0.0 | 0.1 | 99.5 | 99.9 | 100.0 |
| Red | 17780 | 17711 | 99.6 | 0.0 | 0.0 | 0.0 | 0.5 | 97.6 | 99.5 | 99.9 |

By confidence:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 5 s % | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|---|
| high | 28208 | 28075 | 99.5 | 0.0 | 0.0 | 0.0 | 0.3 | 99.7 | 99.9 | 99.9 |
| medium | 92737 | 92250 | 99.5 | 0.0 | 0.0 | 0.0 | 0.5 | 98.4 | 99.8 | 99.9 |
| low | 4440 | 4409 | 99.3 | 0.0 | 0.0 | 0.0 | 0.7 | 98.0 | 99.2 | 99.9 |

The 1.4 % of observed rows more than 5 s from zero were not examined row by row, so no cause is claimed for them.

### Bracketed rows (stop never seen STOPPED_AT)

Here LAMP has information we do not: it is built from every feed update, we poll at 1 min or 15 s.
LAMP's time is the first `STOPPED_AT`; ours is interpolated by distance.

By gap between the two anchors:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| <=30 | 1681 | 1340 | 79.7 | 9.6 | 50.6 | 1.1 | 10.3 | 82.8 | 93.1 |
| 31-60 | 14141 | 13127 | 92.8 | 12.8 | 33.7 | 11.3 | 12.7 | 86.0 | 99.2 |
| 61-120 | 20497 | 19449 | 94.9 | 16.4 | 36.1 | 14.1 | 12.2 | 82.1 | 99.0 |
| >120 | 2874 | 2539 | 88.3 | 27.2 | 49.3 | 23.6 | 11.9 | 58.1 | 93.3 |

By line:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| Blue | 3331 | 3319 | 99.6 | 16.7 | 34.0 | 16.5 | 17.4 | 83.5 | 99.9 |
| Green-B | 4556 | 4499 | 98.7 | 19.3 | 43.0 | 18.4 | 18.3 | 73.6 | 97.8 |
| Green-C | 6141 | 4969 | 80.9 | 15.4 | 39.0 | 10.3 | 11.2 | 78.9 | 98.4 |
| Green-D | 4665 | 4564 | 97.8 | 20.4 | 43.5 | 16.7 | 13.6 | 73.2 | 97.8 |
| Green-E | 7156 | 5939 | 83.0 | 17.8 | 40.2 | 11.5 | 7.2 | 76.6 | 97.5 |
| Mattapan | 3990 | 3940 | 98.7 | 7.4 | 16.3 | 4.6 | 3.8 | 98.8 | 99.9 |
| Orange | 6298 | 6187 | 98.2 | 13.4 | 26.6 | 12.8 | 11.0 | 94.2 | 99.7 |
| Red | 3056 | 3038 | 99.4 | 19.0 | 44.9 | 19.0 | 21.4 | 73.1 | 96.6 |

The bias is positive (ours later) on every line and grows with the gap. Interpolating between an
observation before the stop and one after the vehicle left puts the arrival inside the dwell. LAMP
reports its own dwell at the stop (median 36 s on these rows), and subtracting half of it from the
difference leaves almost nothing:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| ALL | 35655 | 35655 | 100.0 | 8.4 | 25.7 | -1.9 | -3.1 | 92.9 | 98.7 |

By line:

| group | ours | matched | match % | median abs s | p90 abs s | median bias s | mean bias s | within 30 s % | within 60 s % |
|---|---|---|---|---|---|---|---|---|---|
| Blue | 3264 | 3264 | 100.0 | 8.5 | 17.1 | -2.7 | -2.6 | 99.1 | 99.8 |
| Green-B | 4410 | 4410 | 100.0 | 8.3 | 26.2 | 2.8 | 2.3 | 93.6 | 99.6 |
| Green-C | 4751 | 4751 | 100.0 | 8.8 | 29.1 | -2.1 | -2.4 | 90.4 | 99.2 |
| Green-D | 4434 | 4434 | 100.0 | 9.9 | 32.0 | -0.6 | -3.9 | 88.5 | 98.1 |
| Green-E | 5699 | 5699 | 100.0 | 9.9 | 36.3 | -2.8 | -8.5 | 87.3 | 95.8 |
| Mattapan | 3925 | 3925 | 100.0 | 6.1 | 16.9 | -2.8 | -4.2 | 98.1 | 99.8 |
| Orange | 6154 | 6154 | 100.0 | 5.9 | 22.8 | -4.4 | -6.5 | 95.3 | 100.0 |
| Red | 3018 | 3018 | 100.0 | 9.5 | 24.3 | 8.5 | 7.0 | 94.6 | 97.2 |

(The match-rate columns of the last two tables are not meaningful: the rows are restricted to
matched pairs that have a LAMP dwell.)

## Findings

1. **Match rate 97.9 %** of our rows with an arrival time have a LAMP counterpart, 99.5 % for
   `high`, 97.8 % for `medium`, 95.0 % for `low`. The shortfall is concentrated in bracketed
   stops on Green-C and Green-E that LAMP has no row for.
2. **Observed stops agree with LAMP to the second** once the definitions are aligned (median
   difference 0 s, 98.6 % within 5 s, 99.7 % within 30 s). This confirms the reconstruction reads
   the same `STOPPED_AT` as LAMP for the same trip and stop, and that trip, stop, direction and
   service-date matching and the UTC conversion are consistent. It does not test the midpoint, since LAMP reports the interval's
   end.
3. **The -21 s median bias is definitional, not a bug.** Ours is the interval midpoint, LAMP's is
   the interval end: bias = -(interval width / 2). At 15 s sampling intervals are narrower and the
   median bias is -12 s. Treat MBTA's published time as the latest the vehicle could have arrived.
4. **A systematic bias in bracketed arrivals: ours is later, median +13 s (mean +12 s), and +24 s when
   the anchors are more than 120 s apart.** The evidence is that subtracting half of LAMP's dwell
   removes it (median bias +13.1 s to -1.9 s, median absolute difference 15.6 s to 8.4 s, within 30 s
   81.9 % to 92.9 %). Cause: distance interpolation treats the vehicle as moving at constant speed
   through the stop, but it stops and waits there. The existing hand-check saw one such case
   (36 s later than the read, "a vehicle that dwelled"); this shows it is the typical case.
5. **Sampling at 15 s helps, but the sample is small.** `high` is 56 % of rows at 15 s against
   13 % at 1 min, and the median absolute difference falls from 24.7 s to 12.5 s, almost all of it
   from narrower intervals. Four hours of one evening is not a basis for a stronger claim.
6. **No bug found** in matching of trip, stop, direction or service date. The 232 rows more than
   10 min from their LAMP counterpart (0.14 %) are reused trip ids where the two sides chose
   different runs, or genuine disagreements; they are listed in the outcome table, not scored.

## Proposed fix (not applied; the model is unchanged in this phase)

For bracketed stops, subtract half of an expected dwell from the interpolated time (equivalently,
interpolate to the *arrival* at the stop rather than to a point inside its dwell).

- The dwell estimate must not come from LAMP. Candidates, best first: a per-line, per-stop median
  dwell estimated from our own observed stops (first to last `STOPPED_AT` capture within a run is a
  lower bound at 1-minute sampling and nearly exact at 15 s); the scheduled dwell
  (`departure_time - arrival_time` in `stop_times`) where it is non-zero; failing both, a constant
  per line, about 18 s overall (half the 36 s median LAMP dwell).
- Expected effect, using LAMP's dwell as the ceiling: bracketed median bias about 0 s, median
  absolute difference about 8 s, within 30 s about 93 %. A non-LAMP estimate will do somewhat worse.
- It changes `fct_reconstructed_arrivals`, so the fact version goes from 1 to 2 and closed days need
  `rebuild_facts` ([RUNBOOK](RUNBOOK.md#closed-day-facts)). Re-run `make validate` before and after;
  the bracketed "minus half of dwell" table above is the acceptance check.
- No change is proposed to the observed midpoint: it minimises the worst-case error over the
  interval. If a consumer wants LAMP-compatible numbers, expose the interval end
  (`arrival_ts + interval_width_s / 2`) as a separate column rather than moving the estimate.

## Limitations

- **Not ground truth.** Same feed, same vehicle reports. A vehicle that reports `STOPPED_AT` late,
  or a feed outage, affects both sides alike and cancels in the comparison. A position error is
  invisible here.
- **LAMP's fallback.** Where LAMP has no VehiclePosition it uses the last StopTimeUpdate arrival,
  a prediction. The file does not say which rows those are, so some bracketed comparisons are
  against a prediction.
- **Observed rows are validated only for identity**, not accuracy (see "What the definitions
  imply"). The honest error bound for an observed stop is half its interval, whatever LAMP says.
- **Five days, one of them partial on each end** (22 Nov from about 11:30 local, 26 Nov to the
  evening), over the Thanksgiving week, so ridership, dwell and service patterns are not typical.
- **The 15 s period** is about four hours of one evening.
- **Pass 2 is rare** (52 pairs) and tuned by the 600 s gate; the gate itself is a judgment, and
  232 candidate pairs were rejected by it.
- **`stop_id` is the platform id in both datasets.** A stop renamed in static GTFS between the two
  builds would appear as unmatched.

## What would change the confidence thresholds

Current: `high` = observed with interval <= 30 s, `medium` = observed <= 120 s or bracketed with
gap <= 120 s, `low` otherwise.

- **Observed `high` / `medium` / `low`:** LAMP cannot tell us. The error bound is half the interval
  (at most 15 s, 60 s and over 60 s). The thresholds describe interval width honestly; nothing here
  argues for moving them. If the product wants "within 30 s of the true arrival", the observed
  `medium` band (31-120 s intervals) does not guarantee it; the bound is 60 s at the top of the band.
- **Bracketed `medium`** (gap <= 120 s) is within 30 s of LAMP for 83.6 % of rows and within 60 s
  for 98.9 %. After the dwell correction the 30 s figure would be about 93 %. Until then, raising it
  to "within 30 s for 90 %" would put bracketed `medium` below the bar.
- **Bracketed `low`** (gap > 120 s) is within 30 s for 58.1 % and within 60 s for 93.3 %: it is
  correctly separated from `medium`.
- **A gap <= 30 s** cell in the bracketed table has few rows and a 10 s mean error driven by
  outliers; there is no case for a separate tier.
- **Re-measure** after the dwell fix, and again with several full days of 15 s sampling; at that
  point observed intervals will mostly be <= 30 s and the `medium` band shrinks. Revisit the
  thresholds only then.
