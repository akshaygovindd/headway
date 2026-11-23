-- Headway adherence per line over the last 24 hours and the last 7 days (int_window_hours), all
-- directions and stops combined, computed over every candidate headway of fct_headways whose
-- later train is scheduled inside the window. Valid headways only for the shares: within band is
-- a ratio of actual to scheduled headway of 0.5 to 1.5, bunched below 0.5, a gap above 1.5.
-- Dropped headways are counted by reason; outage hours are left out and counted in
-- n_excluded_outage; headways in hours with an active service-disruption alert are kept and
-- counted in n_disruption. Coverage: valid headways over scheduled headways of the non-outage
-- hours. Percentages 0-100.
with scheduled as (
    select
        s.line,
        w.window_label,
        sum(s.scheduled_headways) filter (where not f.data_outage) as scheduled_headways
    from {{ ref('int_scheduled_headways') }} s
    join {{ ref('int_window_hours') }} w on w.service_date = s.service_date and w.hour_local = s.hour_local
    join {{ ref('int_hour_flags') }} f on f.service_date = s.service_date and f.hour_local = s.hour_local
    group by 1, 2
),

headways as (
    select
        h.line,
        w.window_label,
        count(*) filter (where not f.data_outage and h.status = 'valid') as n_headways,
        count(*) filter (where not f.data_outage and h.status = 'valid' and d.line is not null) as n_disruption,
        count(*) filter (where not f.data_outage and h.band = 'within') as n_within,
        count(*) filter (where not f.data_outage and h.band = 'bunched') as n_bunched,
        count(*) filter (where not f.data_outage and h.band = 'gap') as n_gap,
        median(h.ratio) filter (where not f.data_outage and h.status = 'valid') as median_ratio,
        quantile_cont(h.ratio, 0.9) filter (where not f.data_outage and h.status = 'valid') as p90_ratio,
        count(*) filter (where not f.data_outage and h.status = 'train_between') as n_dropped_train_between,
        count(*) filter (where not f.data_outage and h.status = 'unproven_order') as n_dropped_unproven_order,
        count(*) filter (where not f.data_outage and h.status = 'no_scheduled_headway') as n_dropped_no_scheduled_headway,
        count(*) filter (where not f.data_outage and h.status = 'implausible') as n_dropped_implausible,
        count(*) filter (where f.data_outage) as n_excluded_outage
    from {{ ref('fct_headways') }} h
    join {{ ref('int_window_hours') }} w on w.service_date = h.service_date and w.hour_local = h.hour_local
    join {{ ref('int_hour_flags') }} f on f.service_date = h.service_date and f.hour_local = h.hour_local
    left join {{ ref('int_disrupted_line_hours') }} d
        on d.line = h.line and d.service_date = h.service_date and d.hour_local = h.hour_local
    group by 1, 2
)

select
    s.line,
    s.window_label,
    coalesce(h.n_headways, 0) as n_headways,
    coalesce(h.n_disruption, 0) as n_disruption,
    100.0 * h.n_within / nullif(h.n_headways, 0) as pct_within_band,
    100.0 * h.n_bunched / nullif(h.n_headways, 0) as pct_bunched,
    100.0 * h.n_gap / nullif(h.n_headways, 0) as pct_gap,
    h.median_ratio,
    h.p90_ratio,
    coalesce(h.n_dropped_train_between, 0) as n_dropped_train_between,
    coalesce(h.n_dropped_unproven_order, 0) as n_dropped_unproven_order,
    coalesce(h.n_dropped_no_scheduled_headway, 0) as n_dropped_no_scheduled_headway,
    coalesce(h.n_dropped_implausible, 0) as n_dropped_implausible,
    coalesce(h.n_excluded_outage, 0) as n_excluded_outage,
    s.scheduled_headways,
    coalesce(h.n_headways, 0) as coverage_numerator,
    s.scheduled_headways as coverage_denominator,
    100.0 * coalesce(h.n_headways, 0) / nullif(s.scheduled_headways, 0) as coverage_pct
from scheduled s
left join headways h on h.line = s.line and h.window_label = s.window_label
