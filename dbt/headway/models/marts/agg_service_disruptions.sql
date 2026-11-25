-- Line-scope service-disruption alerts of the last 24 hours (int_window_hours), one row per line
-- and alert, for the disruption notes of the public site (Release 4). The header text is the
-- public MBTA alert text, unchanged. Only alerts with scope line and is_service_disruption (see
-- int_alert_scopes) are kept. is_active is true when the alert was active in the latest hour of
-- the window. station_ids are the stations the alert covers; the segments it affects are the
-- segments of agg_headway_segment_window (last_24h) that start or end at one of them, as
-- 'direction_id:from_stop_id:to_stop_id' keys. from_station_name and to_station_name are the first
-- and last affected station along direction 0 of the line (position = segment_order of the
-- segment ending at the station, one less for the first stop); null when the line has no
-- direction-0 segments or none of the stations is on them.
with alerts as (
    select
        a.line,
        a.alert_id,
        any_value(a.effect) as effect,
        arg_max(a.header_text, a.last_seen) as header_text,
        min(a.first_seen) as first_seen,
        max(a.last_seen) as last_seen,
        bool_or(w.hour_start = (select max(hour_start) from {{ ref('int_window_hours') }} where window_label = 'last_24h')) as is_active,
        list_distinct(flatten(list(a.station_ids))) as station_ids
    from {{ ref('int_alert_scopes') }} a
    join {{ ref('int_window_hours') }} w
        on w.window_label = 'last_24h' and w.service_date = a.service_date and w.hour_local = a.hour_local
    where a.scope = 'line' and a.is_service_disruption
    group by 1, 2
),

alert_stations as (
    select line, alert_id, unnest(station_ids) as station_id from alerts
),

positions as (
    select line, station_id, min(pos) as pos
    from (
        select s.line, f.station_id, s.segment_order - 1 as pos
        from {{ ref('agg_headway_segment_window') }} s
        join {{ ref('int_stop_stations') }} f on f.stop_id = s.from_stop_id
        where s.window_label = 'last_24h' and s.direction_id = 0
        union all
        select s.line, t.station_id, s.segment_order as pos
        from {{ ref('agg_headway_segment_window') }} s
        join {{ ref('int_stop_stations') }} t on t.stop_id = s.to_stop_id
        where s.window_label = 'last_24h' and s.direction_id = 0
    )
    group by 1, 2
),

ends as (
    select
        x.line,
        x.alert_id,
        arg_min(n.station_name, p.pos) as from_station_name,
        arg_max(n.station_name, p.pos) as to_station_name
    from alert_stations x
    join positions p on p.line = x.line and p.station_id = x.station_id
    join (select distinct station_id, station_name from {{ ref('int_stop_stations') }}) n on n.station_id = x.station_id
    group by 1, 2
),

segments as (
    select
        x.line,
        x.alert_id,
        list(distinct s.direction_id || ':' || s.from_stop_id || ':' || s.to_stop_id
            order by s.direction_id || ':' || s.from_stop_id || ':' || s.to_stop_id) as segment_keys
    from alert_stations x
    join {{ ref('agg_headway_segment_window') }} s
        on s.line = x.line and s.window_label = 'last_24h'
    left join {{ ref('int_stop_stations') }} f on f.stop_id = s.from_stop_id
    left join {{ ref('int_stop_stations') }} t on t.stop_id = s.to_stop_id
    where x.station_id in (f.station_id, t.station_id)
    group by 1, 2
)

select
    a.line,
    a.alert_id,
    a.effect,
    a.header_text,
    a.first_seen,
    a.last_seen,
    a.is_active,
    len(a.station_ids) as n_stations,
    e.from_station_name,
    e.to_station_name,
    coalesce(s.segment_keys, []::varchar[]) as segment_keys
from alerts a
left join ends e on e.line = a.line and e.alert_id = a.alert_id
left join segments s on s.line = a.line and s.alert_id = a.alert_id
