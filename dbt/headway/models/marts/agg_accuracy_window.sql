-- Prediction accuracy per line and horizon bucket over the last 24 hours and the last 7 days
-- (int_window_hours), computed over every error in the window, never from daily medians. Rows
-- are those of fct_prediction_errors whose arrival hour is inside the window. Outage hours are
-- excluded from every statistic and counted in n_excluded_outage; hours with an active
-- service-disruption alert are kept and counted in n_disruption. Coverage: trip stops with an
-- error in the bucket over the scheduled trip stops of the line in the window (outage hours
-- excluded), the same basis as agg_actual_deviation_window. Both sides are placed by the trip
-- stop's scheduled hour: a train running late across an hour boundary has its error scored in the
-- later hour (n and the statistics) but is still one of the scheduled trip stops of the earlier
-- hour, so counting it by arrival hour could put more errors over the window's edge than the
-- schedule has trip stops (coverage above 100%). Percentages are 0-100.
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

numerators as (
    select
        e.line,
        w.window_label,
        e.horizon_bucket,
        count(*) as scheduled_basis_n
    from {{ ref('fct_prediction_errors') }} e
    join {{ ref('int_window_hours') }} w
        on w.service_date = e.service_date and w.hour_local = e.scheduled_hour_local
    join {{ ref('int_hour_flags') }} f
        on f.service_date = e.service_date and f.hour_local = e.scheduled_hour_local and not f.data_outage
    group by 1, 2, 3
),

denominators as (
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
    coalesce(m.scheduled_basis_n, 0) as coverage_numerator,
    coalesce(d.scheduled_trip_stops, 0) as coverage_denominator,
    100.0 * coalesce(m.scheduled_basis_n, 0) / nullif(d.scheduled_trip_stops, 0) as coverage_pct
from stats s
left join numerators m
    on m.line = s.line and m.window_label = s.window_label and m.horizon_bucket = s.horizon_bucket
left join denominators d on d.line = s.line and d.window_label = s.window_label
