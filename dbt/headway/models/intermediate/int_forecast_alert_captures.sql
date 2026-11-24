-- One row per successful alerts capture with the lines under an active line-scope service
-- disruption in it (Release 3, ADR-019). Same rule as int_alert_scopes, evaluated per capture
-- instead of per hour: an alert entity counts when the capture is inside its active period; the
-- alert is line scope when an entity names the route without a stop or it covers two or more
-- stations of the route; it is a disruption by its effect or a shuttle/suspension header.
-- fetched_at is when the capture existed, so a forecast at t reads the latest row with
-- fetched_at <= t and never a later one.
with entities as (
    select
        a.capture_id,
        a.route_id as line,
        a.alert_id,
        a.effect,
        a.header_text,
        a.stop_id,
        coalesce(s.parent_station, a.stop_id) as station_id
    from {{ ref('stg_service_alerts') }} a
    left join {{ ref('dim_stop') }} s on s.stop_id = a.stop_id
    where a.route_id is not null
        and (a.active_start is null or a.active_start <= a.fetched_at)
        and (a.active_end is null or a.fetched_at <= a.active_end)
),

per_alert as (
    select
        capture_id,
        line,
        alert_id,
        (
            any_value(effect) in ('DETOUR', 'NO_SERVICE', 'REDUCED_SERVICE', 'SIGNIFICANT_DELAYS', 'MODIFIED_SERVICE')
            or regexp_matches(coalesce(any_value(header_text), ''), 'shuttle|suspen', 'i')
        ) as is_service_disruption,
        (bool_or(stop_id is null) or count(distinct station_id) >= 2) as is_line_scope
    from entities
    group by 1, 2, 3
),

disrupted as (
    select capture_id, list(distinct line order by line) as disrupted_lines
    from per_alert
    where is_service_disruption and is_line_scope
    group by capture_id
)

select
    c.capture_id,
    c.fetched_at,
    coalesce(d.disrupted_lines, []::varchar[]) as disrupted_lines
from {{ ref('int_captures') }} c
left join disrupted d on d.capture_id = c.capture_id
where c.feed = 'alerts' and c.status = 'ok'
