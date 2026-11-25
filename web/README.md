# Headway public site

Next.js (App Router, TypeScript strict, Tailwind) site that renders the Release 4 metric API
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
| `/` line cards | `/lines`, `/lines/{line}/headways` (Red, Orange, Blue) or `/reliability` (others), `/incidents` |
| `/lines/[line]` | `headways`, `reliability`, `accuracy`, `countdown`; the trend compares `last_24h` with `last_7d` |
| `/incidents` | `/incidents` |
| `/forecast` | `/forecast/{line}` |
| `/pipeline` | `/pipeline/health`, `/slo` |
| `/how-we-measure` | `/metrics` |
| `/data` | none (generated from `src/data/contracts.json`) |
| `/about` | none |

## Trust rules

- A number reaches the page only through `Metric` (`src/components/trust`), which renders it with a
  `CoverageBadge`. A value that is null, has a `withheld_reason`, or sits on a row under 50% coverage
  renders `Not enough data: coverage X%` and none of the value. Tests in `test/trust.test.tsx` enforce this.
- `Freshness` says "updated N min ago" from `freshness.age_minutes` and marks data older than 60 minutes as stale.
- `DisruptionChip` and `OutageLabel` state their meaning in words. Outage hours are Headway's own data gaps,
  not service failures.
- Pipeline and SLO figures count Headway's own runs and events, so they show the events and days they are
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
first line is a comment with the metric definition URL and the export time; then the header.
Columns: the row's identifying labels, then `value`, `unit`, `coverage_pct`, `withheld_reason`,
`freshness` (the data's `exported_at`). A withheld value is an empty cell with the reason, never a
number. Text cells starting with `=`, `+`, `-` or `@` get a leading apostrophe so spreadsheets do
not run them. The builder is `src/lib/csv.ts`.

## Accessibility and keyboard tests

`test/a11y.test.tsx` runs axe-core on every page (rendered with the fixture API, inside the real
layout). `test/keyboard.test.tsx` checks tab order through the skip link, main nav and theme
toggle, and Enter/Space on the toggle. jsdom cannot compute layout, so axe's colour-contrast rule
is off in tests.

## Performance

Pages are server-rendered and prerendered (revalidated every 5 minutes). The only client
components are the theme toggle and the CSV button (`test/client-js.test.ts` enforces this). The
JavaScript below is the Next.js and React runtime needed to hydrate those two; the CSV button adds
about 1 kB on the pages that have it. Sizes from `npm run build:mock` then
`node scripts/page-sizes.mjs` (gzip):

| Route | HTML (gzip) | First-load JS (gzip) | Scripts |
| --- | --- | --- | --- |
| `/about` | 3.4 kB | 174.9 kB | 9 |
| `/data` | 20.9 kB | 174.9 kB | 9 |
| `/forecast` | 6.8 kB | 174.9 kB | 9 |
| `/how-we-measure` | 4.1 kB | 174.9 kB | 9 |
| `/incidents` | 4.4 kB | 176.0 kB | 10 |
| `/` | 4.2 kB | 174.9 kB | 9 |
| `/lines/Blue` | 6.0 kB | 176.0 kB | 10 |
| `/lines/Green-B` | 6.7 kB | 176.0 kB | 10 |
| `/lines/Green-C` | 6.7 kB | 176.0 kB | 10 |
| `/lines/Green-D` | 6.7 kB | 176.0 kB | 10 |
| `/lines/Green-E` | 6.7 kB | 176.0 kB | 10 |
| `/lines/Mattapan` | 6.7 kB | 176.0 kB | 10 |
| `/lines/Orange` | 6.7 kB | 176.0 kB | 10 |
| `/lines/Red` | 6.7 kB | 176.0 kB | 10 |
| `/pipeline` | 4.2 kB | 174.9 kB | 9 |

The share image (`/opengraph-image`, `/twitter-image`) is rendered at build time with `next/og`.
