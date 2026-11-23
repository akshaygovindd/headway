-- Actual deviation from the timetable per line, direction, service date and scheduled hour:
-- reconstructed arrival minus scheduled arrival, high and medium confidence arrivals only.
-- Rows exist for hours inside the vehicle_positions observation window. Outage hours keep a row
-- with data_outage = true, empty statistics and the excluded count. A deviation beyond an hour
-- is more likely a wrong trip match than a late train: counted in n_implausible, left out of the
-- statistics. Coverage: arrivals used over scheduled trip stops of the hour. Percentages 0-100;
-- pct_on_time_window is the share within -60 to +300 seconds.
with scheduled as (
    select line, direction_id, service_date, hour_local, count(*) as scheduled_trip_stops
    from {{ ref('int_scheduled_stop_times') }}
    group by 1, 2, 3, 4
),

arrivals as (
    select
        line,
        direction_id,
        service_date,
        sched_hour_local as hour_local,
        count(*) as n_all,
        count(*) filter (where abs(deviation_s) > 3600) as n_implausible,
        count(*) filter (where abs(deviation_s) <= 3600) as n_arrivals,
        median(deviation_s) filter (where abs(deviation_s) <= 3600) as median_deviation_s,
        quantile_cont(deviation_s, 0.9) filter (where abs(deviation_s) <= 3600) as p90_deviation_s,
        count(*) filter (where abs(deviation_s) <= 3600 and deviation_s between -60 and 300) as n_in_window
    from {{ ref('int_usable_arrivals') }}
    where is_nearest_segment
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
    case when coalesce(f.data_outage, false) then 0 else coalesce(a.n_arrivals, 0) end as coverage_numerator,
    s.scheduled_trip_stops as coverage_denominator,
    case when not coalesce(f.data_outage, false) then 100.0 * coalesce(a.n_arrivals, 0) / s.scheduled_trip_stops end
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
