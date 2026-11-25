-- ETT approximation per line, direction and time band over the last 24 hours and the last 7 days
-- (int_window_hours), for the trip-time table of a line page (Release 4). Same trips, outage and
-- implausible rules and coverage as agg_ett_window (see ett_trips()), split by direction and time
-- band (am_peak, midday, pm_peak, evening, other; by the scheduled arrival at the second stop),
-- all day types combined. A band with scheduled trips but no measured ETT keeps a row with
-- n_trips 0 (coverage 0), so "no trips measured" is distinguishable from "no row". The plan
-- comparison: median_scheduled_s is the median scheduled running time of the measured trips,
-- median_actual_s the median of scheduled plus ETT, median_ett_s the median ETT itself (actual
-- minus plan). Adapted approximation, not an official measure.
with flagged as (
{{ ett_trips() }}
)

select
    f.line,
    f.direction_id,
    f.time_band,
    w.window_label,
    count(*) filter (where not f.data_outage) as n_scheduled_trips,
    count(*) filter (where f.data_outage) as n_excluded_outage,
    count(*) filter (where not f.data_outage and abs(f.ett_s) > 3600) as n_implausible,
    count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as n_trips,
    count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600 and f.service_disruption) as n_disruption,
    median(f.ett_s) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as median_ett_s,
    quantile_cont(f.ett_s, 0.9) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as p90_ett_s,
    median(f.scheduled_run_s) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as median_scheduled_s,
    median(f.scheduled_run_s + f.ett_s) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as median_actual_s,
    count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as coverage_numerator,
    count(*) filter (where not f.data_outage) as coverage_denominator,
    100.0 * count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600)
        / nullif(count(*) filter (where not f.data_outage), 0) as coverage_pct
from flagged f
join {{ ref('int_window_hours') }} w on w.service_date = f.service_date and w.hour_local = f.first_hour
group by 1, 2, 3, 4
