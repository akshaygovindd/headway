-- Service level objectives per service date (docs/SLO.md): for every objective, the good and total
-- events of the day, the share that was good, whether the target was met and how much of the error
-- budget the day used (bad events over the bad events the target allows; above 1 is overspent).
--   capture_completeness  minutes in which poll 0 of all three feeds was captured, over the minutes
--                         of the day from the first capture to the end of the measurement
--   gold_freshness        hours at whose start the newest exported gold was at most `threshold`
--                         minutes old, from the first recorded export
--   transform_success     scheduled hourly slots with a run that exported and published gold
--   arrival_coverage      scheduled subway trip stops in hours without a data outage and without an
--                         active service disruption on the line, with a high or medium arrival
--   arrival_coverage_disrupted  the same for line hours under an active service disruption:
--                         reported, with the same target, but not counted toward the objective
-- The end of the measurement is the slo_as_of var, else the latest captured minute. Events of the
-- day still in progress are counted up to it, so an open day is partial.
with computed as (
    with params as (
        select
            date_trunc('minute', {% if var('slo_as_of', none) is not none %}cast('{{ var("slo_as_of") }}' as timestamptz){% else %}(select max(scheduled_at) from {{ ref('stg_manifests') }}){% endif %}) as as_of,
            (select date_trunc('minute', min(scheduled_at)) from {{ ref('stg_manifests') }}) as first_minute
    ),

    targets as (
        select slo_name, cast(target_pct as double) as target_pct, cast(threshold as double) as threshold
        from {{ ref('slo_targets') }}
    ),

    days as (
        select
            d.service_date,
            (cast(d.service_date as timestamp) + to_hours({{ var('service_day_cutoff_hour') }})) at time zone '{{ var("timezone") }}' as day_start,
            (cast(d.service_date + 1 as timestamp) + to_hours({{ var('service_day_cutoff_hour') }})) at time zone '{{ var("timezone") }}' as day_end
        from (select distinct service_date from {{ ref('int_captures') }}) d
    ),

    -- capture completeness: every minute of the day within the measurement
    minutes as (
        select
            d.service_date,
            unnest(generate_series(
                greatest(date_trunc('minute', d.day_start), p.first_minute),
                least(d.day_end - interval 1 minute, p.as_of),
                interval 1 minute
            )) as minute
        from days d cross join params p
    ),

    captured as (
        select capture_minute, count(distinct feed) filter (where status = 'ok' and poll_index = 0) as feeds_ok
        from {{ ref('int_captures') }}
        group by capture_minute
    ),

    capture_events as (
        select
            'capture_completeness' as slo_name,
            m.service_date,
            count(*) filter (where coalesce(c.feeds_ok, 0) = 3) as good_events,
            count(*) as total_events
        from minutes m
        left join captured c on c.capture_minute = m.minute
        group by m.service_date
    ),

    -- gold freshness: the age of the newest export at the start of each hour
    exports as (
        select gold_exported_at
        from {{ ref('stg_transform_runs') }}
        where gold_exported_at is not null
    ),

    -- from the first hour after the first recorded export; nothing before any export exists
    hour_starts as (
        select
            d.service_date,
            unnest(generate_series(
                greatest(date_trunc('hour', d.day_start), f.first_hour),
                least(date_trunc('hour', d.day_end - interval 1 minute), date_trunc('hour', p.as_of)),
                interval 1 hour
            )) as hour_start
        from days d
        cross join params p
        cross join (select date_trunc('hour', min(gold_exported_at)) + interval 1 hour as first_hour from exports) f
        where f.first_hour is not null
    ),

    freshness_events as (
        select
            'gold_freshness' as slo_name,
            h.service_date,
            count(*) filter (where age.newest >= h.hour_start - to_minutes(cast((select threshold from targets where slo_name = 'gold_freshness') as bigint))) as good_events,
            count(*) as total_events
        from hour_starts h
        left join lateral (
            select max(gold_exported_at) as newest from exports e where e.gold_exported_at <= h.hour_start
        ) age on true
        group by h.service_date
    ),

    -- transform success: one scheduled slot per hour; a slot counts once its run is recorded (the
    -- run is written at its end, about 50 minutes before the measurement reaches the next slot)
    slots as (
        select
            {{ service_date_from_ts('s.slot') }} as service_date,
            s.slot
        from (
            select unnest(generate_series(
                (select date_trunc('hour', min(started_at - interval 20 minute)) from {{ ref('stg_transform_runs') }}),
                (select date_trunc('hour', p.as_of - interval 50 minute) from params p),
                interval 1 hour
            )) as slot
        ) s
    ),

    good_slots as (
        select distinct slot
        from {{ ref('stg_transform_runs') }}
        where task = 'scheduled' and gold_exported_at is not null and gold_published
    ),

    transform_events as (
        select
            'transform_success' as slo_name,
            s.service_date,
            count(g.slot) as good_events,
            count(*) as total_events
        from slots s
        left join good_slots g on g.slot = s.slot
        group by s.service_date
    ),

    -- arrival coverage, from the hourly actual-deviation coverage of non-outage hours
    arrival_events as (
        select
            case when service_disruption then 'arrival_coverage_disrupted' else 'arrival_coverage' end as slo_name,
            service_date,
            cast(sum(coverage_numerator) as bigint) as good_events,
            cast(sum(coverage_denominator) as bigint) as total_events
        from {{ ref('agg_actual_deviation_hourly') }}
        where not data_outage
        group by 1, 2
    ),

    events as (
        select * from capture_events
        union all select * from freshness_events
        union all select * from transform_events
        union all select * from arrival_events
    )

    select
        e.slo_name,
        e.service_date,
        t.target_pct,
        e.good_events,
        e.total_events,
        100.0 * e.good_events / nullif(e.total_events, 0) as actual_pct,
        100.0 * e.good_events / nullif(e.total_events, 0) >= t.target_pct as met,
        100.0 * (e.total_events - e.good_events) / nullif((100.0 - t.target_pct) * e.total_events, 0) as error_budget_used,
        e.slo_name <> 'arrival_coverage_disrupted' as counts_toward_slo
    from events e
    join targets t on t.slo_name = case when e.slo_name = 'arrival_coverage_disrupted' then 'arrival_coverage' else e.slo_name end
    where e.total_events > 0
)

{{ history_union('agg_slo_daily') }}
