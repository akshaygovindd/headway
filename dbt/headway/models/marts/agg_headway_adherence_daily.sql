-- Headway adherence per line, direction and service date, computed over every candidate headway
-- of the day (hours by the later train's scheduled arrival), all stops combined. Same rules as
-- agg_headway_adherence_hourly: valid headways only for the shares (within 0.5 to 1.5 times the
-- scheduled headway, bunched below, gap above), dropped headways counted by reason, outage hours
-- left out and counted in n_excluded_outage, headways in hours with an active service-disruption
-- alert kept and counted in n_disruption. Coverage: valid headways over scheduled headways of the
-- non-outage hours. Percentages 0-100. The daily row is written to history once the day closes.
with computed as (
    with scheduled as (
        select
            s.line,
            s.direction_id,
            s.service_date,
            sum(s.scheduled_headways) filter (where not f.data_outage) as scheduled_headways
        from {{ ref('int_scheduled_headways') }} s
        join {{ ref('int_hour_flags') }} f on f.service_date = s.service_date and f.hour_local = s.hour_local
        group by 1, 2, 3
    ),

    headways as (
        select
            h.line,
            h.direction_id,
            h.service_date,
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
        join {{ ref('int_hour_flags') }} f on f.service_date = h.service_date and f.hour_local = h.hour_local
        left join {{ ref('int_disrupted_line_hours') }} d
            on d.line = h.line and d.service_date = h.service_date and d.hour_local = h.hour_local
        group by 1, 2, 3
    )

    select
        s.line,
        s.direction_id,
        s.service_date,
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
    left join headways h
        on h.line = s.line
        and h.direction_id is not distinct from s.direction_id
        and h.service_date = s.service_date
)

{{ history_union('agg_headway_adherence_daily') }}
