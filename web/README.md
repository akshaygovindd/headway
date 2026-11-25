# Headway public site

Next.js (App Router, TypeScript strict, Tailwind) site, design system v2, that renders the Release 4 metric API
([docs/API.md](../docs/API.md)). Server components fetch the API and revalidate every 300 s, the
cache lifetime of the gold export. Not deployed yet; see the Vercel note in
[docs/DEPLOY.md](../docs/DEPLOY.md#public-site-on-vercel-not-executed).

## Run it locally

From the repository root, in two terminals:

```sh
make demo-data   # once: builds the demo gold in .local/demo
make api-dev     # the API on http://localhost:8000 (uvicorn, reads the demo gold over file://)
make web-dev     # the site on http://localhost:3000 (installs npm packages on first run)
```

No Python needed? `node web/scripts/mock-api.mjs` serves the fixtures in `test/fixtures` on port 8000.
Port 3000 busy: `cd web && HEADWAY_API_URL=http://localhost:8000 npx next dev -p 3100`.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `HEADWAY_API_URL` | `http://localhost:8000` | Base URL of the metric API, no trailing path. Server side only. |
| `NEXT_PUBLIC_REPO_URL` | the project repository | Where "How this is measured" links point (`docs/METRICS.md#...`). |
| `NEXT_PUBLIC_SITE_URL` | `http://localhost:3000` | Public origin used for canonical URLs, Open Graph/Twitter cards, `sitemap.xml` and `robots.txt`. Set it in the deploy. |

The browser never calls the API: pages are rendered on the server, so the API needs no CORS entry
for this site when it is rendered by Next.js.

## Checks

```sh
cd web
npm run lint && npm run typecheck && npm test
npm run build:mock   # production build against the fixtures, no network
```

`npm run ci` runs all four. The CI `web` job does the same.

## Pages and the API

| Page | Endpoints |
| --- | --- |
| `/` hero, network, trust strip, line cards, accuracy | `/meta`, `/lines`, `/lines/{line}/headways` (Red, Orange, Blue) or `/reliability` (others), `/lines/{line}/segments`, `/lines/{line}/accuracy`, `/lines/{line}/disruptions`, `/incidents`, `/pipeline/heartbeat`, `/pipeline/checks` |
| `/lines/[line]` | `headways`, `reliability`, `accuracy`, `countdown`, `segments` (24 h and 7 d), `trip-time` (24 h and 7 d), `spacing-by-hour`, `disruptions`, `marey?direction=0`, `/pipeline/health`, `/incidents` |
| `/incidents` | `/incidents` |
| `/forecast` | `/forecast/{line}` |
| `/pipeline` | `/pipeline/health`, `/slo` |
| `/how-we-measure` | `/metrics` |
| `/data` | none (generated from `src/data/contracts.json`) |
| `/about` | none |

The R4 endpoints (`segments`, `marey`, `heartbeat`, `trip-time`, `disruptions`) answer 404 `not_available` until the transform
runs with `enable_r4`; the sections that need them then show "not published yet" and the rest of the page renders.

## Design system v2

Light only. Tokens live in `src/app/globals.css`: page `#F4F5F7`, surfaces `#FFFFFF`, ink `#10141A`, muted
`#566070`. Overpass 700/800 for headings, Inter for body text and every number (`tabular-nums`),
JetBrains Mono only for ids, SQL and timestamps (class `mono`). The variable-font subsets are committed in
`src/fonts` and loaded with `next/font/local`, so the build needs no network. Line colours (`src/lib/lines.ts`)
are fills and badges only (RL OL BL B C D E M). The spacing ramp (even `#3346B8`, some bunching `#8F6BD6`,
bunched or gaps `#C93A8B`) always has a text legend. Status patterns each come with words: partial is an amber
dot and "partial", withheld is a grey hatch and "Not enough data · N% cov.", our data gap is a darker hatch,
a service disruption is a dashed brown outline with a bus icon on cream.

Pure, unit-tested logic is in `src/lib`: `coverage.ts` (full, partial 50 to under 70, withheld), `health.ts`
(bucket and legend), `hero.ts` (the generated sentence; it gives ranges and never orders lines), `network.ts`
(segment rows and disrupted stretches), `marey.ts` (geometry, bunching and long-wait annotations),
`heartbeat.ts` (capture share), `checks.ts` (the trust strip's checks figure from `/pipeline/checks`), `linedata.ts` (trip-time and spacing-by-hour states), `time.ts` (everything is Eastern).

The 24 h / 7 d toggle and the Marey phone view need no script: both windows are rendered and radios (`:has`)
or `:target` pick one. "Spacing by hour" (`/spacing-by-hour`, 05:00 to 01:00 ET) colours each hour with the
spacing ramp from `pct_within_band`; our data gaps are the dark hatch ("our data gap"), withheld hours the grey
hatch, and hours with scheduled trains but no valid headway a dashed outline ("no headways"). The trip-time
table (`/trip-time`) has a row per direction and time band: "No trips measured yet" only for status `no_trips`,
the withheld hatch for `withheld`, and median and 90th percentile against the plan, planned and actual running
time for `measured`.

Disruption chips (line cards, line banner, network diagram label) show the public MBTA alert header from
`/disruptions`, else a segment's `disruption_header`, as plain text: cut off visually with the full text in the
title and accessible name. Only when no header text is available is "stops skipped" inferred from open
incidents, labelled "Possible stop skipped (inferred from incidents)".

## Trust rules

- A number reaches the page only through `Metric` (`src/components/trust`), which renders it as a button
  that opens the "How this was measured" popover, with the coverage glyph (`CoverageBadge`: a five-segment
  meter and "N% cov.") under it. A value that is null, has a `withheld_reason`, or sits on a row under 50%
  coverage renders a hatched `Not enough data · N% cov.` and none of the value. Counts of Headway's own runs
  (`OwnMetric`) are buttons too but have no coverage glyph. Tests in `test/trust.test.tsx` enforce this.
- `Freshness` says "updated N min ago" from `freshness.age_minutes` and marks data older than 60 minutes as stale.
- `DisruptionChip` and `OutageLabel` state their meaning in words. Outage hours are Headway's own data gaps,
  not service failures.
- Pipeline figures (daily runs, SLOs, the trust strip's checks) count Headway's own runs, events and checks, so they show the events and days they are
  based on instead of a coverage badge.
- If the API is down the page shows a friendly message; a table missing from the export shows "not published yet".
  Pages are static with revalidation, so a page built while the API was down keeps its error message for at most 300 s.

## Data contracts (`/data`)

`/data` lists every public gold table the API serves: columns, types, nullability, grain,
freshness, coverage rule and quality checks, with links to the METRICS.md anchor and the API
endpoint. It is built from `src/data/contracts.json`, which `scripts/build-contracts.mjs`
generates from `dbt/headway/models/marts/_aggregates.yml`, `src/headway_api/queries.py` and
`docs/METRICS.md` (the script fails if an anchor is missing or `queries.py` changes shape).
The JSON is committed, so the web build needs no Python.

```sh
npm run contracts         # regenerate after changing dbt docs or API tables
npm run contracts:check   # fails if the committed JSON is stale (also run by npm test)
```

Column types are **derived from naming conventions** (dbt declares none) and nullability from
`not_null` tests; the page says so. Authoritative types will come from the export manifest.

## CSV export

The Line and Incidents pages have a "Download CSV" button for exactly the rows on the page. The
file is data only (a header row, then rows, no comment line); the metric definition URL and the
export time are in a separate `*.metadata.txt` from the "Metadata" button. Bands with no trips are
rows with a reason, and the Line CSV includes spacing by hour (outage hours too). Percentages are
percent points: a skipped-stop share of 0.013 is written `1.3` with unit `pct`. Columns: the row's identifying labels, then `value`, `unit`, `coverage_pct`, `withheld_reason`,
`freshness` (the data's `exported_at`). A withheld value is an empty cell with the reason, never a
number. Text cells starting with `=`, `+`, `-` or `@` get a leading apostrophe so spreadsheets do
not run them. The builder is `src/lib/csv.ts`.

## "How this was measured" popover

`MetricPopover` (client) sits once in the layout and listens for clicks on `button[data-k]`. A native
`<dialog>` shows the definition from `src/lib/definitions.ts`, n, coverage, window, what was left out, the
METRICS.md anchor and the SQL of the gold model. Tab stays inside, Escape closes, focus returns to the number,
and at 480 px or less it is a bottom sheet. Per-line notes live in `src/data/metric-notes.json` (Blue's note
links to the MEASUREMENTS.md entry).

## Accessibility and keyboard tests

`test/a11y.test.tsx` runs axe-core on every page (rendered with the fixture API, inside the real
layout). `test/keyboard.test.tsx` checks tab order through the skip link and main nav, and
`test/popover.test.tsx` the popover (open with Enter and Space, focus trap, Escape, focus return). jsdom cannot compute layout, so axe's colour-contrast rule
is off in tests; `test/contrast.test.ts` replaces it: it reads the light tokens from `globals.css`, lists every
text/background pair the site renders (declaring which are large text) and fails below 4.5:1 (3:1 for large
text), naming the pair and its ratio. A new token must be added to its table.

## Performance

Pages are server-rendered and prerendered (revalidated every 5 minutes). The only client
components are the CSV button, the metric popover and `error.tsx` (Next requires error boundaries to be client components; it renders nothing unless a page fails). `test/client-js.test.ts` enforces this list. The
JavaScript below is the Next.js and React runtime needed to hydrate them. HTML is larger than before because the
network diagram, station strips and Marey chart are server-rendered SVG and markup. Sizes from `npm run build:mock`
then `node scripts/page-sizes.mjs` (gzip):

| Route | HTML (gzip) | First-load JS (gzip) | Scripts |
| --- | --- | --- | --- |
| `/about` | 3.7 kB | 177.4 kB | 9 |
| `/data` | 31.6 kB | 177.4 kB | 9 |
| `/forecast` | 8.2 kB | 177.4 kB | 9 |
| `/how-we-measure` | 4.4 kB | 177.4 kB | 9 |
| `/incidents` | 5.1 kB | 178.2 kB | 10 |
| `/` | 21.3 kB | 177.4 kB | 9 |
| `/lines/Blue` | 28.3 kB | 178.2 kB | 10 |
| `/lines/Green-B` | 33.8 kB | 178.2 kB | 10 |
| `/lines/Green-C` | 33.4 kB | 178.2 kB | 10 |
| `/lines/Green-D` | 34.6 kB | 178.2 kB | 10 |
| `/lines/Green-E` | 34.7 kB | 178.2 kB | 10 |
| `/lines/Mattapan` | 25.0 kB | 178.2 kB | 10 |
| `/lines/Orange` | 32.7 kB | 178.2 kB | 10 |
| `/lines/Red` | 26.1 kB | 178.2 kB | 10 |
| `/pipeline` | 4.5 kB | 177.4 kB | 9 |

The share image (`/opengraph-image`, `/twitter-image`) is rendered at build time with `next/og`.
