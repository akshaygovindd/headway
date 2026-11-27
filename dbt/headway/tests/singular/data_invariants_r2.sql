{{ config(tags=['r2'], severity='error') }}
-- Impossible values: shares and fractions outside their unit range, negative counts, a median above
-- its p90 or p95, a coverage_pct that is not its own row's numerator / denominator (DATA_QUALITY.md).
{{ data_invariant_violations('r2') }}
