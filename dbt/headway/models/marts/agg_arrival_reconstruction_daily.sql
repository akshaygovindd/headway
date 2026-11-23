-- Arrival reconstruction coverage per service date and line: how many trip stops were
-- reconstructed, by method and confidence, how many broke the monotonic rule, and which observed
-- trips were left out because they could not be placed on a shape. Shares are percentages
-- (0-100) of the reconstructed trip stops (rows of fct_reconstructed_arrivals).
with reconstructed as (
    select
        service_date,
        line,
        count(*) as trip_stops,
        count(distinct trip_instance_key) as trips_reconstructed,
        count(*) filter (where method = 'observed') as n_observed,
        count(*) filter (where method = 'bracketed') as n_bracketed,
        count(*) filter (where method = 'missing') as n_missing,
        count(*) filter (where confidence = 'high') as n_high,
        count(*) filter (where confidence = 'medium') as n_medium,
        count(*) filter (where confidence = 'low') as n_low,
        count(*) filter (where not monotonic_ok) as n_monotonic_violations
    from {{ ref('fct_reconstructed_arrivals') }}
    group by service_date, line
),

excluded as (
    select
        service_date,
        line,
        count(distinct trip_instance_key) filter (where exclusion_reason = 'added_trip') as added_trips_excluded,
        count(distinct trip_instance_key) filter (where exclusion_reason = 'unscheduled_trip') as unscheduled_trips_excluded,
        count(distinct trip_instance_key) filter (where exclusion_reason = 'no_shape') as no_shape_trips_excluded
    from {{ ref('int_vehicle_stop_events') }}
    where exclusion_reason is not null
    group by service_date, line
)

select
    coalesce(r.service_date, x.service_date) as service_date,
    coalesce(r.line, x.line) as line,
    coalesce(r.trip_stops, 0) as trip_stops,
    coalesce(r.trips_reconstructed, 0) as trips_reconstructed,
    coalesce(r.n_observed, 0) as n_observed,
    coalesce(r.n_bracketed, 0) as n_bracketed,
    coalesce(r.n_missing, 0) as n_missing,
    coalesce(r.n_high, 0) as n_high,
    coalesce(r.n_medium, 0) as n_medium,
    coalesce(r.n_low, 0) as n_low,
    100.0 * coalesce(r.n_high, 0) / nullif(r.trip_stops, 0) as share_high,
    100.0 * coalesce(r.n_medium, 0) / nullif(r.trip_stops, 0) as share_medium,
    100.0 * coalesce(r.n_low, 0) / nullif(r.trip_stops, 0) as share_low,
    100.0 * coalesce(r.n_missing, 0) / nullif(r.trip_stops, 0) as share_missing,
    coalesce(r.n_monotonic_violations, 0) as n_monotonic_violations,
    coalesce(x.added_trips_excluded, 0) as added_trips_excluded,
    coalesce(x.unscheduled_trips_excluded, 0) as unscheduled_trips_excluded,
    coalesce(x.no_shape_trips_excluded, 0) as no_shape_trips_excluded
from reconstructed r
full outer join excluded x on x.service_date = r.service_date and x.line = r.line
