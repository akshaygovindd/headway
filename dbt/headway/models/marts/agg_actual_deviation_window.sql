-- Actual deviation from the timetable per line over the last 24 hours and the last 7 days
-- (int_window_hours), all directions combined, computed over every reconstructed arrival of the
-- window. Same rules as agg_actual_deviation_hourly: high and medium arrivals, the run nearest
-- the schedule, hours by the scheduled arrival, a deviation beyond an hour counted in
-- n_implausible. Outage hours are left out and their arrivals counted in n_excluded_outage;
-- arrivals in hours with an active service-disruption alert are kept and counted in
-- n_disruption. Coverage: arrivals used over scheduled trip stops of the non-outage hours.
-- Percentages 0-100; pct_on_time_window is the share within -60 to +300 seconds.
with scheduled as (
    select
        s.line,
        w.window_label,
        sum(s.n) filter (where not f.data_outage) as scheduled_trip_stops
    from (
        select line, service_date, hour_local, count(*) as n
        from {{ ref('int_scheduled_stop_times') }}
        group by 1, 2, 3
    ) s
    join {{ ref('int_window_hours') }} w on w.service_date = s.service_date and w.hour_local = s.hour_local
    join {{ ref('int_hour_flags') }} f on f.service_date = s.service_date and f.hour_local = s.hour_local
    group by 1, 2
),

arrivals as (
    select
        a.line,
        w.window_label,
        count(*) filter (where f.data_outage) as n_excluded_outage,
        count(*) filter (where not f.data_outage and abs(a.deviation_s) > 3600) as n_implausible,
        count(*) filter (where not f.data_outage and abs(a.deviation_s) <= 3600) as n_arrivals,
        count(*) filter (where not f.data_outage and abs(a.deviation_s) <= 3600 and d.line is not null) as n_disruption,
        median(a.deviation_s) filter (where not f.data_outage and abs(a.deviation_s) <= 3600) as median_deviation_s,
        quantile_cont(a.deviation_s, 0.9) filter (where not f.data_outage and abs(a.deviation_s) <= 3600) as p90_deviation_s,
        count(*) filter (where not f.data_outage and abs(a.deviation_s) <= 3600 and a.deviation_s between -60 and 300) as n_in_window
    from {{ ref('int_usable_arrivals') }} a
    join {{ ref('int_window_hours') }} w on w.service_date = a.service_date and w.hour_local = a.sched_hour_local
    join {{ ref('int_hour_flags') }} f on f.service_date = a.service_date and f.hour_local = a.sched_hour_local
    left join {{ ref('int_disrupted_line_hours') }} d
        on d.line = a.line and d.service_date = a.service_date and d.hour_local = a.sched_hour_local
    where a.is_nearest_segment
    group by 1, 2
)

select
    s.line,
    s.window_label,
    coalesce(a.n_arrivals, 0) as n_arrivals,
    coalesce(a.n_excluded_outage, 0) as n_excluded_outage,
    coalesce(a.n_implausible, 0) as n_implausible,
    coalesce(a.n_disruption, 0) as n_disruption,
    a.median_deviation_s,
    a.p90_deviation_s,
    100.0 * a.n_in_window / nullif(a.n_arrivals, 0) as pct_on_time_window,
    s.scheduled_trip_stops,
    coalesce(a.n_arrivals, 0) as coverage_numerator,
    s.scheduled_trip_stops as coverage_denominator,
    100.0 * coalesce(a.n_arrivals, 0) / nullif(s.scheduled_trip_stops, 0) as coverage_pct
from scheduled s
left join arrivals a on a.line = s.line and a.window_label = s.window_label
