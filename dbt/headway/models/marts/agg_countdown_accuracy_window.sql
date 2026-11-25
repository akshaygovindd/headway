-- Countdown accuracy of MBTA's published predictions per line, station, countdown bucket (1, 3, 5
-- and 10 minutes) and window (last 24 hours, last 7 days), exact over every countdown in the
-- window (see fct_countdown_outcomes). A countdown is hit within 60 s (120 s) when the train
-- arrived within that of the predicted time; a confirmed non-arrival is a miss; unknown outcomes
-- are left out of n and counted in n_unknown. Windows hold the countdowns whose predicted arrival
-- hour is inside them. Outage hours are excluded from every figure and counted in
-- n_excluded_outage; hours with a service-disruption alert are kept and counted in n_disruption.
-- Coverage: counted countdowns over the scheduled trip stops of the station in the window (outage
-- hours excluded), the basis of agg_accuracy_window. Percentages are 0-100. All figures are
-- counts that add up exactly across stations of a line.
with stats as (
    select
        o.line,
        o.station_id,
        any_value(o.station_name) as station_name,
        w.window_label,
        o.countdown_min,
        count(*) filter (where not o.data_outage and o.outcome <> 'unknown') as n,
        count(*) filter (where not o.data_outage and o.outcome = 'arrived') as n_arrived,
        count(*) filter (where not o.data_outage and o.outcome = 'non_arrival') as n_non_arrival,
        count(*) filter (where not o.data_outage and o.outcome = 'unknown') as n_unknown,
        count(*) filter (where o.data_outage) as n_excluded_outage,
        count(*) filter (
            where not o.data_outage and o.outcome <> 'unknown' and o.service_disruption
        ) as n_disruption,
        count(*) filter (where not o.data_outage and o.hit_60) as n_within_60s,
        count(*) filter (where not o.data_outage and o.hit_120) as n_within_120s
    from {{ ref('fct_countdown_outcomes') }} o
    join {{ ref('int_window_hours') }} w
        on w.service_date = o.predicted_service_date and w.hour_local = o.hour_local
    group by o.line, o.station_id, w.window_label, o.countdown_min
),

denominators as (
    select
        s.line,
        coalesce(st.station_id, s.stop_id) as station_id,
        w.window_label,
        count(*) as scheduled_trip_stops
    from {{ ref('int_scheduled_stop_times') }} s
    join {{ ref('int_window_hours') }} w on w.service_date = s.service_date and w.hour_local = s.hour_local
    join {{ ref('int_hour_flags') }} f
        on f.service_date = s.service_date and f.hour_local = s.hour_local and not f.data_outage
    left join {{ ref('int_stop_stations') }} st on st.stop_id = s.stop_id
    group by 1, 2, 3
)

select
    s.line,
    s.station_id,
    s.station_name,
    s.window_label,
    s.countdown_min,
    s.n,
    s.n_arrived,
    s.n_non_arrival,
    s.n_unknown,
    s.n_excluded_outage,
    s.n_disruption,
    s.n_within_60s,
    s.n_within_120s,
    100.0 * s.n_within_60s / nullif(s.n, 0) as pct_within_60s,
    100.0 * s.n_within_120s / nullif(s.n, 0) as pct_within_120s,
    s.n as coverage_numerator,
    coalesce(d.scheduled_trip_stops, 0) as coverage_denominator,
    100.0 * s.n / nullif(d.scheduled_trip_stops, 0) as coverage_pct
from stats s
left join denominators d
    on d.line = s.line and d.station_id = s.station_id and d.window_label = s.window_label
