-- The MBTA's speed restrictions as typed in by hand from its public page (seed
-- official_speed_restrictions, ADR-025), typed. Committed with no rows until captured: then this
-- view is empty and every consumer says "official list not captured yet". Rows with a blank
-- restriction_id or line are ignored. Each row carries its source_url and captured_on.
select
    trim(restriction_id) as restriction_id,
    trim(line) as line,
    trim(location_text) as location_text,
    nullif(trim(direction), '') as direction,
    try_cast(nullif(trim(speed_limit_mph), '') as integer) as speed_limit_mph,
    nullif(trim(reason), '') as reason,
    try_cast(nullif(trim(date_reported), '') as date) as date_reported,
    trim(source_url) as source_url,
    try_cast(nullif(trim(captured_on), '') as date) as captured_on
from {{ ref('official_speed_restrictions') }}
where nullif(trim(restriction_id), '') is not null and nullif(trim(line), '') is not null
