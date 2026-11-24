-- Service days that count toward the forecast training gate (Release 3, ADR-019), per line: the
-- day's arrival coverage is the share of scheduled trip stops of non-outage hours with a usable
-- (high or medium) reconstructed arrival, the number the arrival_coverage SLO uses, taken from
-- agg_actual_deviation_daily over both directions. A day passes at forecast_gate.min_coverage_pct.
select
    line,
    service_date,
    case when dayofweek(service_date) in (0, 6) then 'weekend' else 'weekday' end as day_type,
    cast(sum(coverage_numerator) as bigint) as n_arrivals,
    cast(sum(coverage_denominator) as bigint) as n_scheduled_trip_stops,
    100.0 * sum(coverage_numerator) / nullif(sum(coverage_denominator), 0) as coverage_pct,
    coalesce(100.0 * sum(coverage_numerator) / nullif(sum(coverage_denominator), 0)
        >= {{ var('forecast_gate')['min_coverage_pct'] }}, false) as meets_coverage
from {{ ref('agg_actual_deviation_daily') }}
group by line, service_date
