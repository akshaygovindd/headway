-- Announced elevator and escalator outages per station over the last 24 hours and the last 7 days
-- (Release 4, Phase 4.5; METRICS.md "Elevator and escalator outages"). One row per station of the
-- scheduled subway network and window, so "no outage announced" is a row, not an absence.
--   outage_hours  hours in which an announced outage covered the station, in the window's hours that
--                 are not data outages (int_window_hours, int_hour_flags); overlapping alerts of one
--                 station are merged, so no time is counted twice
--   n_outages     alerts that overlapped those hours, split by facility
--   currently_out / n_currently_out / current_outage_text   in the latest alerts capture, whatever the window
--   longest_open_outage_h   hours since the start of the oldest open outage (no end time), not clipped
--   coverage_pct  window hours with data and not a data outage, over the hours of the window (24, 168)
-- Announced outages, not the facility's own status.
with windows as (
    select * from (values ('last_24h', 24), ('last_7d', 168)) as w (window_label, n_hours)
),

win_hours as (
    select
        w.window_label,
        h.hour_start,
        h.hour_start + interval 1 hour as hour_end,
        coalesce(f.data_outage, false) as data_outage
    from {{ ref('int_window_hours') }} h
    join windows w on w.window_label = h.window_label
    left join {{ ref('int_hour_flags') }} f on f.service_date = h.service_date and f.hour_local = h.hour_local
),

coverage as (
    select
        w.window_label,
        w.n_hours,
        count(*) filter (where not h.data_outage) as evaluated_hours
    from windows w
    left join win_hours h on h.window_label = w.window_label
    group by 1, 2
),

stations as (
    select
        st.station_id,
        any_value(st.station_name) as station_name,
        list(distinct s.line order by s.line) as lines
    from {{ ref('int_scheduled_stop_times') }} s
    join {{ ref('int_stop_stations') }} st on st.stop_id = s.stop_id
    group by st.station_id
),

clipped as (
    select
        o.station_id,
        o.alert_id,
        o.facility,
        h.window_label,
        h.hour_start,
        greatest(o.start_ts, h.hour_start) as s,
        least(o.end_ts, h.hour_end) as e
    from {{ ref('int_accessibility_outages') }} o
    join win_hours h on o.start_ts < h.hour_end and o.end_ts > h.hour_start
    where not h.data_outage
),

merged as (
    select
        *,
        max(e) over (
            partition by station_id, window_label, hour_start
            order by s, e, alert_id
            rows between unbounded preceding and 1 preceding
        ) as prev_max_e
    from clipped
),

hours as (
    select
        station_id,
        window_label,
        sum(epoch(greatest(e - greatest(s, coalesce(prev_max_e, s)), interval 0 second))) / 3600.0 as outage_hours
    from merged
    group by 1, 2
),

counts as (
    select
        station_id,
        window_label,
        count(distinct alert_id) as n_outages,
        count(distinct alert_id) filter (where facility = 'elevator') as n_elevator_outages,
        count(distinct alert_id) filter (where facility = 'escalator') as n_escalator_outages
    from clipped
    group by 1, 2
),

current_state as (
    select
        station_id,
        count(*) filter (where still_active) as n_currently_out,
        max(age_h) filter (where is_open) as longest_open_outage_h,
        list(distinct header_text order by header_text) filter (where still_active and header_text is not null) as current_outage_text
    from {{ ref('int_accessibility_outages') }}
    group by station_id
)

select
    s.station_id,
    s.station_name,
    s.lines,
    c.window_label,
    coalesce(n.n_outages, 0) as n_outages,
    coalesce(n.n_elevator_outages, 0) as n_elevator_outages,
    coalesce(n.n_escalator_outages, 0) as n_escalator_outages,
    round(coalesce(h.outage_hours, 0), 2) as outage_hours,
    coalesce(cs.n_currently_out, 0) > 0 as currently_out,
    coalesce(cs.n_currently_out, 0) as n_currently_out,
    round(cs.longest_open_outage_h, 1) as longest_open_outage_h,
    coalesce(cs.current_outage_text, []::varchar[]) as current_outage_text,
    c.evaluated_hours as coverage_numerator,
    c.n_hours as coverage_denominator,
    round(100.0 * c.evaluated_hours / nullif(c.n_hours, 0), 1) as coverage_pct
from stations s
cross join coverage c
left join counts n on n.station_id = s.station_id and n.window_label = c.window_label
left join hours h on h.station_id = s.station_id and h.window_label = c.window_label
left join current_state cs on cs.station_id = s.station_id
