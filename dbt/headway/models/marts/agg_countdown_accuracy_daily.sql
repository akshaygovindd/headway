-- Countdown accuracy of MBTA's published predictions per line, station, service date (of the
-- predicted arrival) and countdown bucket: the daily history of agg_countdown_accuracy_window,
-- with the same definitions (see fct_countdown_outcomes and that model). Closed days are kept as
-- immutable history (ADR-015).
with computed as (
with stats as (
    select
        line,
        station_id,
        any_value(station_name) as station_name,
        predicted_service_date as service_date,
        countdown_min,
        count(*) filter (where not data_outage and outcome <> 'unknown') as n,
        count(*) filter (where not data_outage and outcome = 'arrived') as n_arrived,
        count(*) filter (where not data_outage and outcome = 'non_arrival') as n_non_arrival,
        count(*) filter (where not data_outage and outcome = 'unknown') as n_unknown,
        count(*) filter (where data_outage) as n_excluded_outage,
        count(*) filter (
            where not data_outage and outcome <> 'unknown' and service_disruption
        ) as n_disruption,
        count(*) filter (where not data_outage and hit_60) as n_within_60s,
        count(*) filter (where not data_outage and hit_120) as n_within_120s
    from {{ ref('fct_countdown_outcomes') }}
    group by line, station_id, predicted_service_date, countdown_min
),

denominators as (
    select
        s.line,
        coalesce(st.station_id, s.stop_id) as station_id,
        s.service_date,
        count(*) as scheduled_trip_stops
    from {{ ref('int_scheduled_stop_times') }} s
    join {{ ref('int_hour_flags') }} f
        on f.service_date = s.service_date and f.hour_local = s.hour_local and not f.data_outage
    left join {{ ref('int_stop_stations') }} st on st.stop_id = s.stop_id
    group by 1, 2, 3
)

select
    s.line,
    s.station_id,
    s.station_name,
    s.service_date,
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
    on d.line = s.line and d.station_id = s.station_id and d.service_date = s.service_date
)

{{ history_union('agg_countdown_accuracy_daily') }}
