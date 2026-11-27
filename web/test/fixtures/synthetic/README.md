# Synthetic fixtures

Hand-made answers for edge states the real gold snapshot (`tests/fixtures/gold_snapshot`) does not
have. Each file carries a `_synthetic` field saying what it stands for. Everything else the site
renders comes from `../real`, written by the real API (`scripts/build_web_fixtures.py`).

- **Red withheld during a shuttle**: every `lines__Red__*` file and the Red row of `lines`: 35%
  coverage (withheld), a shuttle between Broadway and North Quincy, an outage hour, a line with a
  budget pair that has no trips and one with every band withheld.
- **Open elevator outage and withheld window figures**: `accessibility*`.
- **Slow zones with the official list captured** (minor, moderate and withheld segments):
  `slow-zones*`. The real snapshot has no official list captured yet, which is the "not captured"
  state (`MOCK_SLOW_ZONES=not-captured` serves it). `_slow-zones_not-captured.json` is the not-captured state with flagged segments, loaded by a test.
- **An open incident**: `incidents`.

Add a synthetic file only for a state the real snapshot cannot produce; name it as the mock API
does (`slug` in `web/scripts/mock-api.mjs`).
- **A band with no trips observed**: `lines__Orange__trip-time*` (the real Orange trip time with its
  afternoon-peak band, direction 0, turned into no trips).
