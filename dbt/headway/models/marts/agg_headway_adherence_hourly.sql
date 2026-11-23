-- Headway adherence per line, direction, service date and hour of the later train's scheduled
-- arrival, over all stops of the line. Valid headways only (see fct_headways): within band is a
-- ratio of actual to scheduled headway of 0.5 to 1.5, bunched below 0.5, a gap above 1.5. Dropped
-- headways are counted by reason. Coverage: valid headways over scheduled headways of the hour
-- (scheduled stop times with a predecessor at their stop). Outage hours keep a row with empty
-- statistics; percentages are 0-100.
with scheduled as (
    select line, direction_id, service_date, hour_local, count(*) as scheduled_headways
    from (
        select
            line, direction_id, service_date, hour_local,
            lag(scheduled_arrival_ts) over (
                partition by line, direction_id, service_date, stop_id
                order by scheduled_arrival_ts, trip_instance_key
            ) as previous_ts
        from {{ ref('int_scheduled_stop_times') }}
    )
    where previous_ts is not null
    group by 1, 2, 3, 4
),

headways as (
    select
        line,
        direction_id,
        service_date,
        hour_local,
        count(*) filter (where status = 'valid') as n_headways,
        count(*) filter (where band = 'within') as n_within,
        count(*) filter (where band = 'bunched') as n_bunched,
        count(*) filter (where band = 'gap') as n_gap,
        median(ratio) filter (where status = 'valid') as median_ratio,
        count(*) filter (where status = 'train_between') as n_dropped_train_between,
        count(*) filter (where status = 'unproven_order') as n_dropped_unproven_order,
        count(*) filter (where status = 'no_scheduled_headway') as n_dropped_no_scheduled_headway,
        count(*) filter (where status = 'implausible') as n_dropped_implausible
    from {{ ref('fct_headways') }}
    group by 1, 2, 3, 4
)

select
    s.line,
    s.direction_id,
    s.service_date,
    s.hour_local,
    coalesce(f.data_outage, false) as data_outage,
    d.line is not null as service_disruption,
    case when coalesce(f.data_outage, false) then 0 else coalesce(h.n_headways, 0) end as n_headways,
    case when not coalesce(f.data_outage, false) then 100.0 * h.n_within / nullif(h.n_headways, 0) end as pct_within_band,
    case when not coalesce(f.data_outage, false) then 100.0 * h.n_bunched / nullif(h.n_headways, 0) end as pct_bunched,
    case when not coalesce(f.data_outage, false) then 100.0 * h.n_gap / nullif(h.n_headways, 0) end as pct_gap,
    case when not coalesce(f.data_outage, false) then h.median_ratio end as median_ratio,
    case when coalesce(f.data_outage, false) then 0 else coalesce(h.n_dropped_train_between, 0) end as n_dropped_train_between,
    case when coalesce(f.data_outage, false) then 0 else coalesce(h.n_dropped_unproven_order, 0) end as n_dropped_unproven_order,
    case when coalesce(f.data_outage, false) then 0 else coalesce(h.n_dropped_no_scheduled_headway, 0) end as n_dropped_no_scheduled_headway,
    case when coalesce(f.data_outage, false) then 0 else coalesce(h.n_dropped_implausible, 0) end as n_dropped_implausible,
    case when coalesce(f.data_outage, false)
        then coalesce(h.n_headways, 0) + coalesce(h.n_dropped_train_between, 0) + coalesce(h.n_dropped_unproven_order, 0)
            + coalesce(h.n_dropped_no_scheduled_headway, 0) + coalesce(h.n_dropped_implausible, 0)
        else 0
    end as n_excluded_outage,
    s.scheduled_headways,
    case when coalesce(f.data_outage, false) then 0 else coalesce(h.n_headways, 0) end as coverage_numerator,
    s.scheduled_headways as coverage_denominator,
    case when not coalesce(f.data_outage, false) then 100.0 * coalesce(h.n_headways, 0) / s.scheduled_headways end
        as coverage_pct
from scheduled s
join {{ ref('int_hour_flags') }} f on f.service_date = s.service_date and f.hour_local = s.hour_local
left join headways h
    on h.line = s.line
    and h.direction_id is not distinct from s.direction_id
    and h.service_date = s.service_date
    and h.hour_local = s.hour_local
left join {{ ref('int_disrupted_line_hours') }} d
    on d.line = s.line and d.service_date = s.service_date and d.hour_local = s.hour_local
