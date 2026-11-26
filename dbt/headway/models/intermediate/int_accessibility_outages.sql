-- One announced elevator or escalator outage per alert and station (Release 4, Phase 4.5; METRICS.md
-- "Elevator and escalator outages"). Source: the ACCESSIBILITY_ISSUE service alerts already captured.
-- An alert is repeated in every capture and informs several stops (platforms, the station, doors):
-- the stops are mapped to their station (int_stop_stations; a stop without one, such as a door, is
-- left out) and the captures collapse to one interval per alert and station.
--   sighting   a capture in which the alert is inside its active period (as in int_alert_scopes)
--   start_ts   the alert's own start (active_start, may be years back), else its first sighting
--   end_ts     the latest alerts capture when the alert is in it (still going), else its last sighting
--   still_active  the alert is in the latest successful alerts capture
--   is_open    still_active and the alert has no end time
-- facility is elevator or escalator from the public alert text, else unknown.
with latest as (
    select capture_id, fetched_at
    from {{ ref('int_captures') }}
    where feed = 'alerts' and status = 'ok'
    order by fetched_at desc, capture_id desc
    limit 1
),

sightings as (
    select
        a.alert_id,
        a.capture_id,
        a.fetched_at,
        a.header_text,
        a.active_start,
        a.active_end,
        st.station_id,
        st.station_name
    from {{ ref('stg_service_alerts') }} a
    join {{ ref('int_stop_stations') }} st on st.stop_id = a.stop_id
    where a.effect = 'ACCESSIBILITY_ISSUE'
        and (a.active_start is null or a.active_start <= a.fetched_at)
        and (a.active_end is null or a.fetched_at <= a.active_end)
),

per_alert as (
    select
        alert_id,
        station_id,
        any_value(station_name) as station_name,
        arg_max(header_text, (fetched_at, capture_id)) as header_text,
        arg_max(active_end, (fetched_at, capture_id)) as active_end,
        min(active_start) as active_start,
        min(fetched_at) as first_seen,
        max(fetched_at) as last_seen,
        bool_or(capture_id = (select capture_id from latest)) as still_active
    from sightings
    group by alert_id, station_id
)

select
    alert_id,
    station_id,
    station_name,
    header_text,
    case
        when regexp_matches(coalesce(header_text, ''), 'elevator', 'i') then 'elevator'
        when regexp_matches(coalesce(header_text, ''), 'escalator', 'i') then 'escalator'
        else 'unknown'
    end as facility,
    coalesce(active_start, first_seen) as start_ts,
    case when still_active then (select fetched_at from latest) else last_seen end as end_ts,
    still_active,
    still_active and active_end is null as is_open,
    case when still_active then epoch((select fetched_at from latest) - coalesce(active_start, first_seen)) / 3600.0 end as age_h
from per_alert
