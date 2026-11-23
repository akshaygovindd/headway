-- Service alerts active per line and hour, from the alerts feed (see int_alert_scopes for when an
-- alert is active and what its scope is). Duplicate captures count: an unchanged alert is still
-- active. is_service_disruption marks the alerts that make an hour a "service disruption" for the
-- Release 2 metrics: effect DETOUR, NO_SERVICE, REDUCED_SERVICE, SIGNIFICANT_DELAYS or
-- MODIFIED_SERVICE, or a header text mentioning a shuttle or a suspension. ACCESSIBILITY_ISSUE,
-- STOP_MOVED and every other effect never do, unless the header text says so. scope is `line`
-- for an alert that covers the route or two or more stations of it, `stop` for one station; only
-- line-scope disruptions disrupt the line. n_stops and stop_names describe the stations covered.
with captures as (
    select *, unnest(capture_ids) as capture_id from {{ ref('int_alert_scopes') }}
)

select
    service_date,
    hour_local,
    line,
    effect,
    is_service_disruption,
    scope,
    count(distinct alert_id) as n_active_alerts,
    count(distinct capture_id) as n_captures,
    max(n_stops) as n_stops,
    string_agg(distinct stop_names, ', ' order by stop_names) filter (where scope = 'stop') as stop_names
from captures
group by 1, 2, 3, 4, 5, 6
