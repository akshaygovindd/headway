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
