-- Hours the vehicle positions were observed, with the Release 2 data-outage flag: an hour is an
-- outage when missed_runs reaches the warn threshold of the quality_thresholds seed (3 per hour)
-- or when the vehicle_positions window of the hour is incomplete (fewer than 55 capture minutes).
-- One missed run is not an outage. Metrics leave outage hours out and report them.
with threshold as (
    select warn from {{ ref('quality_thresholds') }} where check_name = 'missed_runs'
),

missed as (
    select service_date, hour_local, value as missed_runs
    from {{ ref('agg_quality_checks_hourly') }}
    where check_name = 'missed_runs'
)

select
    w.service_date,
    w.hour_local,
    coalesce(m.missed_runs, 0) as missed_runs,
    w.window_complete as vehicle_window_complete,
    coalesce(m.missed_runs, 0) >= (select warn from threshold) or not w.window_complete as data_outage
from {{ ref('int_observation_window') }} w
left join missed m on m.service_date = w.service_date and m.hour_local = w.hour_local
where w.feed = 'vehicle_positions'
