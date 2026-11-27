{{ config(tags=['r4'], severity='warn') }}
-- Suspicious values that are not impossible: warn only (DATA_QUALITY.md).
{{ data_suspicious_values('r4') }}
