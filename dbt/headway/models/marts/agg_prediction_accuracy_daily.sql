-- Prediction accuracy per line, service date, horizon bucket and service-disruption label, from
-- fct_prediction_errors. Outage hours are excluded from every statistic and counted in
-- n_excluded_outage. Coverage: trip stops with an error in the bucket over the scheduled trip
-- stops of the same line, day and label (outage hours excluded), the same basis as
-- agg_actual_deviation_daily. The coverage numerator is placed like the denominator: by the trip
-- stop's scheduled hour, which gives its outage flag and its service-disruption label, not by the
-- arrival hour of a train that ran late across an hour boundary. n and the error statistics keep the
-- arrival-hour flags.
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

numerators as (
    select
        e.line,
        e.service_date,
        e.horizon_bucket,
        d.line is not null as service_disruption,
        count(*) as scheduled_basis_n
    from {{ ref('fct_prediction_errors') }} e
    join {{ ref('int_hour_flags') }} f
        on f.service_date = e.service_date and f.hour_local = e.scheduled_hour_local and not f.data_outage
    left join {{ ref('int_disrupted_line_hours') }} d
        on d.line = e.line and d.service_date = e.service_date and d.hour_local = e.scheduled_hour_local
    group by 1, 2, 3, 4
),

denominators as (
    select
        t.line,
        t.service_date,
        d.line is not null as service_disruption,
        count(*) as scheduled_trip_stops
    from {{ ref('int_scheduled_stop_times') }} t
    join {{ ref('int_hour_flags') }} f on f.service_date = t.service_date and f.hour_local = t.hour_local
    left join {{ ref('int_disrupted_line_hours') }} d
        on d.line = t.line and d.service_date = t.service_date and d.hour_local = t.hour_local
    where not f.data_outage
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
    coalesce(m.scheduled_basis_n, 0) as coverage_numerator,
    coalesce(d.scheduled_trip_stops, 0) as coverage_denominator,
    100.0 * coalesce(m.scheduled_basis_n, 0) / nullif(d.scheduled_trip_stops, 0) as coverage_pct
from stats s
left join numerators m
    on m.line = s.line and m.service_date = s.service_date and m.horizon_bucket = s.horizon_bucket
    and m.service_disruption = s.service_disruption
left join denominators d
    on d.line = s.line and d.service_date = s.service_date and d.service_disruption = s.service_disruption
)

{{ history_union('agg_prediction_accuracy_daily') }}
