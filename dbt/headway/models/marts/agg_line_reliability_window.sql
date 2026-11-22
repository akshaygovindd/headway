-- Reliability per line over the last 24 service hours, all directions combined.
-- The window ends at the start of the latest hour in agg_line_reliability_hourly
-- (the export's metrics_as_of) and keeps hours starting within 24 hours of it.
-- Medians and the p90 are computed over the eligible predictions themselves, not
-- averaged from hourly values. Coverage and skipped_share are ratios of sums.
with hours as (
    select
        service_date,
        hour_local,
        (
            cast(service_date as timestamp)
            + to_hours(hour_local + case when hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
        ) at time zone '{{ var("timezone") }}' as hour_start
    from {{ ref('agg_line_reliability_hourly') }}
    group by service_date, hour_local
),

window_hours as (
    select service_date, hour_local
    from hours
    where hour_start > (select max(hour_start) from hours) - interval 24 hour
),

hourly as (
    select
        h.line,
        sum(h.coverage_numerator) as coverage_numerator,
        sum(h.observable_trip_stops) as observable_trip_stops,
        sum(h.skipped_stop_count) as skipped_stop_count,
        sum(h.implausible_deviation_count) as implausible_deviation_count
    from {{ ref('agg_line_reliability_hourly') }} h
    join window_hours w on w.service_date = h.service_date and w.hour_local = h.hour_local
    group by h.line
),

eligible as (
    select
        e.line,
        count(*) as n_eligible,
        median(e.predicted_deviation_s) as median_predicted_deviation_s,
        quantile_cont(e.predicted_deviation_s, 0.9) as p90_predicted_deviation_s,
        median(abs(e.revision_s)) as median_abs_revision_s
    from {{ ref('int_eligible_predictions') }} e
    join window_hours w on w.service_date = e.service_date and w.hour_local = e.hour_local
    group by e.line
)

select
    h.line,
    'last_24h' as window_label,
    e.median_predicted_deviation_s,
    e.p90_predicted_deviation_s,
    e.median_abs_revision_s,
    coalesce(e.n_eligible, 0) as n_eligible,
    h.implausible_deviation_count,
    h.coverage_numerator,
    h.observable_trip_stops as coverage_denominator,
    100.0 * h.coverage_numerator / h.observable_trip_stops as coverage_pct,
    h.skipped_stop_count,
    h.skipped_stop_count / h.observable_trip_stops as skipped_share
from hourly h
left join eligible e on e.line = h.line
where h.observable_trip_stops > 0
