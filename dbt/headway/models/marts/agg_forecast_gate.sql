-- Training gate per line (Release 3, ADR-019): whether enough days of usable data exist to train a
-- forecast for the line. A service day counts when its arrival coverage reaches
-- forecast_gate.min_coverage_pct (int_forecast_gate_days). The gate is passed with at least
-- forecast_gate.min_days counting days, of which at least forecast_gate.min_weekend_days are
-- weekend days. Internal: not exported.
select
    line,
    count(*) as n_service_days_seen,
    count(*) filter (where meets_coverage) as n_days,
    count(*) filter (where meets_coverage and day_type = 'weekend') as n_weekend_days,
    min(service_date) filter (where meets_coverage) as first_day,
    max(service_date) filter (where meets_coverage) as last_day,
    {{ var('forecast_gate')['min_days'] }} as min_days,
    {{ var('forecast_gate')['min_weekend_days'] }} as min_weekend_days,
    {{ var('forecast_gate')['min_coverage_pct'] }} as min_coverage_pct,
    count(*) filter (where meets_coverage) >= {{ var('forecast_gate')['min_days'] }}
        and count(*) filter (where meets_coverage and day_type = 'weekend') >= {{ var('forecast_gate')['min_weekend_days'] }}
        as gate_passed
from {{ ref('int_forecast_gate_days') }}
group by line
