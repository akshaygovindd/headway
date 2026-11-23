-- ETT approximation per line, direction, service date, time band, day type and
-- service-disruption label. Per trip: (arrival at the second-to-last stop minus arrival at the
-- second stop) minus the scheduled running time between the same stops. Both stops need a high or
-- medium reconstructed arrival in the same run. Time band by the scheduled arrival at the second
-- stop (local): am_peak 06:30-09:00, midday 09:00-15:30, pm_peak 15:30-18:30, evening 18:30-24:00,
-- other otherwise. Trips with a data-outage hour at either stop are excluded and counted; an ETT
-- beyond an hour is counted in n_implausible and left out of the statistics. Coverage: trips with
-- an ETT over scheduled trips of the group. Adapted approximation, not an official measure.
with flagged as (
{{ ett_trips() }}
)

select
    line,
    direction_id,
    service_date,
    time_band,
    day_type,
    service_disruption,
    count(*) filter (where not data_outage) as n_scheduled_trips,
    count(*) filter (where data_outage) as n_excluded_outage,
    count(*) filter (where not data_outage and abs(ett_s) > 3600) as n_implausible,
    count(*) filter (where not data_outage and abs(ett_s) <= 3600) as n_trips,
    median(ett_s) filter (where not data_outage and abs(ett_s) <= 3600) as median_ett_s,
    quantile_cont(ett_s, 0.9) filter (where not data_outage and abs(ett_s) <= 3600) as p90_ett_s,
    count(*) filter (where not data_outage and abs(ett_s) <= 3600) as coverage_numerator,
    count(*) filter (where not data_outage) as coverage_denominator,
    100.0 * count(*) filter (where not data_outage and abs(ett_s) <= 3600)
        / nullif(count(*) filter (where not data_outage), 0) as coverage_pct
from flagged
group by 1, 2, 3, 4, 5, 6
