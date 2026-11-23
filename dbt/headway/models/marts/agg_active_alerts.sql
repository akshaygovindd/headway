-- Service alerts active per line and hour, from the alerts feed. An alert counts in an hour when a
-- capture of that hour carried it inside its active period (active_start <= fetched_at <=
-- active_end, an open start or end meaning none; an alert without any active period is always
-- active, as in GTFS-realtime). Duplicate captures count: an unchanged alert is still
-- active. is_service_disruption marks the alerts that make an hour a "service disruption" for the
-- Release 2 metrics: effect DETOUR, NO_SERVICE, REDUCED_SERVICE, SIGNIFICANT_DELAYS or
-- MODIFIED_SERVICE, or a header text mentioning a shuttle or a suspension. ACCESSIBILITY_ISSUE,
-- STOP_MOVED and every other effect never do, unless the header text says so.
select
    c.service_date,
    c.hour_local,
    a.route_id as line,
    a.effect,
    (
        a.effect in ('DETOUR', 'NO_SERVICE', 'REDUCED_SERVICE', 'SIGNIFICANT_DELAYS', 'MODIFIED_SERVICE')
        or regexp_matches(coalesce(a.header_text, ''), 'shuttle|suspen', 'i')
    ) as is_service_disruption,
    count(distinct a.alert_id) as n_active_alerts,
    count(distinct a.capture_id) as n_captures
from {{ ref('stg_service_alerts') }} a
join {{ ref('int_captures') }} c on c.capture_id = a.capture_id
where a.route_id is not null
    and (a.active_start is null or a.active_start <= a.fetched_at)
    and (a.active_end is null or a.fetched_at <= a.active_end)
group by 1, 2, 3, 4, 5
