-- Active service alerts per line, hour and alert, with the scope of what they cover. An alert is
-- active in an hour when a capture of that hour carried it inside its active period (an open start
-- or end meaning none; no period at all is always active, as in GTFS-realtime). Scope:
--   line  an informed entity names the route without a stop, or the alert covers two or more
--         distinct stations of the line (a section, a branch, a shuttle)
--   stop  the alert covers one station of the line (a closed station, a skipped stop)
-- A station is the parent station of a stop (the platforms of one station count once), else the
-- stop itself. Entities without a route are not attributed to a line and are not used here.
-- is_service_disruption is the effect rule of Release 2 (see METRICS.md); the scope decides
-- whether the whole line or only that station is affected.
with entities as (
    select
        c.service_date,
        c.hour_local,
        a.route_id as line,
        a.alert_id,
        a.capture_id,
        a.effect,
        a.header_text,
        a.fetched_at,
        a.stop_id,
        coalesce(s.parent_station, a.stop_id) as station_id,
        coalesce(p.stop_name, s.stop_name) as station_name
    from {{ ref('stg_service_alerts') }} a
    join {{ ref('int_captures') }} c on c.capture_id = a.capture_id
    left join {{ ref('dim_stop') }} s on s.stop_id = a.stop_id
    left join {{ ref('dim_stop') }} p on p.stop_id = s.parent_station
    where a.route_id is not null
        and (a.active_start is null or a.active_start <= a.fetched_at)
        and (a.active_end is null or a.fetched_at <= a.active_end)
),

per_alert as (
    select
        service_date,
        hour_local,
        line,
        alert_id,
        arg_max(effect, (fetched_at, capture_id)) as effect,
        arg_max(header_text, (fetched_at, capture_id)) as header_text,
        min(fetched_at) as first_seen,
        max(fetched_at) as last_seen,
        (
            arg_max(effect, (fetched_at, capture_id)) in ('DETOUR', 'NO_SERVICE', 'REDUCED_SERVICE', 'SIGNIFICANT_DELAYS', 'MODIFIED_SERVICE')
            or regexp_matches(coalesce(arg_max(header_text, (fetched_at, capture_id)), ''), 'shuttle|suspen', 'i')
        ) as is_service_disruption,
        bool_or(stop_id is null) as route_level,
        count(distinct station_id) as n_stops,
        list(distinct station_id order by station_id) filter (where station_id is not null) as station_ids,
        string_agg(distinct station_name, ', ' order by station_name) filter (where station_name is not null) as stop_names,
        list(distinct capture_id order by capture_id) as capture_ids
    from entities
    group by 1, 2, 3, 4
)

select
    *,
    case when route_level or n_stops >= 2 then 'line' else 'stop' end as scope
from per_alert
