-- The eligible prediction of each scheduled trip stop: its last-snapshot pick
-- (int_prediction_picks) unless the deviation is implausible (more than an hour
-- from the schedule). Implausible picks still count toward coverage.
select * exclude (is_implausible_deviation)
from {{ ref('int_prediction_picks') }}
where not is_implausible_deviation
