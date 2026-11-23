-- Actual deviation from the timetable per line, direction, service date and scheduled hour:
-- reconstructed arrival minus scheduled arrival, high and medium confidence arrivals only.
-- Rows exist for hours inside the vehicle_positions observation window. Outage hours keep a row
-- with data_outage = true, empty statistics and the excluded count. A deviation beyond an hour
-- is more likely a wrong trip match than a late train: counted in n_implausible, left out of the
-- statistics. Coverage: arrivals used over scheduled trip stops of the hour. Percentages 0-100;
-- pct_on_time_window is the share within -60 to +300 seconds.
-- Trip stops at a station under a stop-level service-disruption alert (a closed station, see
-- int_stop_alert_flags) are left out of the scheduled count and of the arrivals, and counted in
-- n_stop_excluded: the line stays in the objective, only that station's trip stops do not.
with scheduled as (
    select
        t.line,
        t.direction_id,
        t.service_date,
        t.hour_local,
        count(*) filter (where fl.station_id is null) as scheduled_trip_stops,
        count(*) filter (where fl.station_id is not null) as n_stop_excluded
    from {{ ref('int_scheduled_stop_times') }} t
    left join {{ ref('dim_stop') }} ds on ds.stop_id = t.stop_id
    left join {{ ref('int_stop_alert_flags') }} fl
        on fl.line = t.line and fl.service_date = t.service_date and fl.hour_local = t.hour_local
        and fl.station_id = coalesce(ds.parent_station, t.stop_id)
    group by 1, 2, 3, 4
),

arrivals as (
    select
        u.line,
        u.direction_id,
        u.service_date,
        u.sched_hour_local as hour_local,
        count(*) as n_all,
        count(*) filter (where abs(deviation_s) > 3600) as n_implausible,
        count(*) filter (where abs(deviation_s) <= 3600) as n_arrivals,
        median(deviation_s) filter (where abs(deviation_s) <= 3600) as median_deviation_s,
        quantile_cont(deviation_s, 0.9) filter (where abs(deviation_s) <= 3600) as p90_deviation_s,
        count(*) filter (where abs(deviation_s) <= 3600 and deviation_s between -60 and 300) as n_in_window
    from {{ ref('int_usable_arrivals') }} u
    left join {{ ref('dim_stop') }} ds on ds.stop_id = u.stop_id
    left join {{ ref('int_stop_alert_flags') }} fl
        on fl.line = u.line and fl.service_date = u.service_date and fl.hour_local = u.sched_hour_local
        and fl.station_id = coalesce(ds.parent_station, u.stop_id)
    where u.is_nearest_segment and fl.station_id is null
    group by 1, 2, 3, 4
)

select
    s.line,
    s.direction_id,
    s.service_date,
    s.hour_local,
    coalesce(f.data_outage, false) as data_outage,
    d.line is not null as service_disruption,
    case when coalesce(f.data_outage, false) then 0 else coalesce(a.n_arrivals, 0) end as n_arrivals,
    case when coalesce(f.data_outage, false) then coalesce(a.n_all, 0) else 0 end as n_excluded_outage,
    case when coalesce(f.data_outage, false) then 0 else coalesce(a.n_implausible, 0) end as n_implausible,
    case when not coalesce(f.data_outage, false) then a.median_deviation_s end as median_deviation_s,
    case when not coalesce(f.data_outage, false) then a.p90_deviation_s end as p90_deviation_s,
    case when not coalesce(f.data_outage, false) then 100.0 * a.n_in_window / nullif(a.n_arrivals, 0) end
        as pct_on_time_window,
    s.scheduled_trip_stops,
    s.n_stop_excluded,
    case when coalesce(f.data_outage, false) then 0 else coalesce(a.n_arrivals, 0) end as coverage_numerator,
    s.scheduled_trip_stops as coverage_denominator,
    case when not coalesce(f.data_outage, false) then 100.0 * coalesce(a.n_arrivals, 0) / nullif(s.scheduled_trip_stops, 0) end
        as coverage_pct
from scheduled s
join {{ ref('int_hour_flags') }} f on f.service_date = s.service_date and f.hour_local = s.hour_local
left join arrivals a
    on a.line = s.line
    and a.direction_id is not distinct from s.direction_id
    and a.service_date = s.service_date
    and a.hour_local = s.hour_local
left join {{ ref('int_disrupted_line_hours') }} d
    on d.line = s.line and d.service_date = s.service_date and d.hour_local = s.hour_local
