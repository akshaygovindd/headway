-- Prediction accuracy per line and horizon bucket over the last 24 hours and the last 7 days
-- (int_window_hours), computed over every error in the window, never from daily medians. Rows
-- are those of fct_prediction_errors whose arrival hour is inside the window. Outage hours are
-- excluded from every statistic and counted in n_excluded_outage; hours with an active
-- service-disruption alert are kept and counted in n_disruption. Coverage: trip stops with an
-- error in the bucket over the high or medium reconstructed trip stops of the line in the window
-- (outage hours excluded). Percentages are 0-100.
with stats as (
    select
        e.line,
        w.window_label,
        e.horizon_bucket,
        count(*) filter (where not e.data_outage) as n,
        count(*) filter (where e.data_outage) as n_excluded_outage,
        count(*) filter (where not e.data_outage and e.service_disruption) as n_disruption,
        median(e.abs_error_s) filter (where not e.data_outage) as median_abs_error_s,
        quantile_cont(e.abs_error_s, 0.9) filter (where not e.data_outage) as p90_abs_error_s,
        avg(e.abs_error_s) filter (where not e.data_outage) as mae_s,
        100.0 * count(*) filter (where not e.data_outage and e.abs_error_s <= 60)
            / nullif(count(*) filter (where not e.data_outage), 0) as pct_within_60s,
        100.0 * count(*) filter (where not e.data_outage and e.abs_error_s <= 120)
            / nullif(count(*) filter (where not e.data_outage), 0) as pct_within_120s
    from {{ ref('fct_prediction_errors') }} e
    join {{ ref('int_window_hours') }} w
        on w.service_date = e.arrival_service_date and w.hour_local = e.hour_local
    group by 1, 2, 3
),

denominators as (
    select u.line, w.window_label, count(*) as reconstructed_trip_stops
    from {{ ref('int_usable_arrivals') }} u
    join {{ ref('int_window_hours') }} w
        on w.service_date = u.arrival_service_date and w.hour_local = u.arrival_hour_local
    where u.is_nearest_segment and not u.data_outage
    group by 1, 2
)

select
    s.line,
    s.window_label,
    s.horizon_bucket,
    s.n,
    s.n_excluded_outage,
    s.n_disruption,
    s.median_abs_error_s,
    s.p90_abs_error_s,
    s.mae_s,
    s.pct_within_60s,
    s.pct_within_120s,
    s.n as coverage_numerator,
    coalesce(d.reconstructed_trip_stops, 0) as coverage_denominator,
    100.0 * s.n / nullif(d.reconstructed_trip_stops, 0) as coverage_pct
from stats s
left join denominators d on d.line = s.line and d.window_label = s.window_label
