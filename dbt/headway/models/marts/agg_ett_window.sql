-- ETT approximation per line over the last 24 hours and the last 7 days (int_window_hours), all
-- directions, time bands and day types combined, computed over every trip of the window (hour of
-- the scheduled arrival at the second stop, as in agg_ett_daily; trips come from ett_trips()).
-- Trips with a data-outage hour at either stop are excluded and counted; an ETT beyond an hour
-- is counted in n_implausible and left out of the statistics; trips in an hour with an active
-- service-disruption alert are kept and counted in n_disruption. Coverage: trips with an ETT over
-- scheduled trips of the line. Adapted approximation, not an official measure.
with flagged as (
{{ ett_trips() }}
)

select
    f.line,
    w.window_label,
    count(*) filter (where not f.data_outage) as n_scheduled_trips,
    count(*) filter (where f.data_outage) as n_excluded_outage,
    count(*) filter (where not f.data_outage and abs(f.ett_s) > 3600) as n_implausible,
    count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as n_trips,
    count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600 and f.service_disruption) as n_disruption,
    median(f.ett_s) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as median_ett_s,
    quantile_cont(f.ett_s, 0.9) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as p90_ett_s,
    count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600) as coverage_numerator,
    count(*) filter (where not f.data_outage) as coverage_denominator,
    100.0 * count(*) filter (where not f.data_outage and abs(f.ett_s) <= 3600)
        / nullif(count(*) filter (where not f.data_outage), 0) as coverage_pct
from flagged f
join {{ ref('int_window_hours') }} w on w.service_date = f.service_date and w.hour_local = f.first_hour
group by 1, 2
