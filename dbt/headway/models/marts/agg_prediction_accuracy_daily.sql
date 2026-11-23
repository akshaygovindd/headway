-- Prediction accuracy per line, service date, horizon bucket and service-disruption label, from
-- fct_prediction_errors. Outage hours are excluded from every statistic and counted in
-- n_excluded_outage. Coverage: trip stops with an error in the bucket over the high or medium
-- reconstructed trip stops of the same line, day and label (outage hours excluded).
-- Percentages are 0-100.
with computed as (
with stats as (
    select
        line,
        service_date,
        horizon_bucket,
        service_disruption,
        count(*) filter (where not data_outage) as n,
        count(*) filter (where data_outage) as n_excluded_outage,
        median(abs_error_s) filter (where not data_outage) as median_abs_error_s,
        avg(abs_error_s) filter (where not data_outage) as mae_s,
        100.0 * count(*) filter (where not data_outage and abs_error_s <= 60)
            / nullif(count(*) filter (where not data_outage), 0) as pct_within_60s,
        100.0 * count(*) filter (where not data_outage and abs_error_s <= 120)
            / nullif(count(*) filter (where not data_outage), 0) as pct_within_120s
    from {{ ref('fct_prediction_errors') }}
    group by 1, 2, 3, 4
),

denominators as (
    select line, service_date, service_disruption, count(*) as reconstructed_trip_stops
    from {{ ref('int_usable_arrivals') }}
    where is_nearest_segment and not data_outage
    group by 1, 2, 3
)

select
    s.line,
    s.service_date,
    s.horizon_bucket,
    s.service_disruption,
    s.n,
    s.n_excluded_outage,
    s.median_abs_error_s,
    s.mae_s,
    s.pct_within_60s,
    s.pct_within_120s,
    s.n as coverage_numerator,
    coalesce(d.reconstructed_trip_stops, 0) as coverage_denominator,
    100.0 * s.n / nullif(d.reconstructed_trip_stops, 0) as coverage_pct
from stats s
left join denominators d
    on d.line = s.line and d.service_date = s.service_date and d.service_disruption = s.service_disruption
)

{{ history_union('agg_prediction_accuracy_daily') }}
