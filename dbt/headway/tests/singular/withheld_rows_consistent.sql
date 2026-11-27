{{ config(tags=['r3'], severity='error') }}
-- A withheld row carries a reason and no value; a row that is not withheld carries no reason and
-- its values (agg_weather_comparison: withheld when either group has too few days).
select condition, metric, withheld, withheld_reason, value_condition, value_other
from {{ ref('agg_weather_comparison') }}
where (withheld and (withheld_reason is null or value_condition is not null or value_other is not null
        or ci_lo_condition is not null or ci_hi_condition is not null
        or ci_lo_other is not null or ci_hi_other is not null))
    or (not withheld and (withheld_reason is not null or value_condition is null or value_other is null))
