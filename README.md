# Headway: Near-Real-Time Transit Reliability & AI Analytics

*Know the gap. Measure the reliability.*

Headway is a data platform for MBTA subway reliability.
It ingests real-time and static transit feeds and curates them into reliability metrics.
Cities are configured through YAML, so the pipeline is not tied to a single agency.

Status: Release 1 in progress

## Quick start

```sh
make demo
```

This replays the bundled one-hour Boston sample, builds the dbt marts, exports the gold
tables and a live snapshot to `.local/demo`, and starts the Streamlit app at
http://localhost:8501. It needs only `uv`; no cloud access.

<!-- screenshot: add an image of the Overview page here -->
*Screenshot: coming soon.*

## Development

```sh
uv sync
make check       # lint, types, unit tests
make e2e         # replay the sample and compare the key tables
make app-test    # Streamlit app tests (uses the `app` dependency group)
```

The app reads gold Parquet files and a live snapshot from `HEADWAY_DATA_URL`
(`file://` or `https://`). For Streamlit Community Cloud use `app/requirements.txt`.
