-- Hourly data-quality checks against warn/fail thresholds from quality_thresholds.
-- Share and coverage values are percentages (0-100); missed_runs is a count per
-- hour; vehicle_count_drop is the percentage fall against the same hour one week
-- earlier and is reported as 'no_baseline' when that hour was not observed.
with hours as (
    select distinct service_date, hour_local from {{ ref('int_captures') }}
),

bounds as (
    select
        date_trunc('minute', min(scheduled_at)) as first_minute,
        date_trunc('minute', max(scheduled_at)) as last_minute
    from {{ ref('stg_manifests') }}
),

-- every minute between the first and last scheduled run, so partial first and
-- last hours are clipped and edges never count as missed
minutes as (
    select
        {{ service_date_from_ts('m.minute') }} as service_date,
        cast(extract(hour from m.minute at time zone '{{ var("timezone") }}') as integer) as hour_local,
        count(*) as expected_runs
    from (
        select unnest(generate_series(first_minute, last_minute, interval 1 minute)) as minute from bounds
    ) m
    group by 1, 2
),

runs as (
    select service_date, hour_local, count(distinct run_id) as observed_runs
    from {{ ref('int_captures') }}
    group by service_date, hour_local
),

missed_runs as (
    select
        m.service_date,
        m.hour_local,
        'missed_runs' as check_name,
        cast(greatest(m.expected_runs - coalesce(r.observed_runs, 0), 0) as double) as value
    from minutes m
    left join runs r on r.service_date = m.service_date and r.hour_local = m.hour_local
),

duplicate_share as (
    select
        service_date,
        hour_local,
        'duplicate_share' as check_name,
        100.0 * avg(is_duplicate_capture::int) as value
    from {{ ref('int_captures') }}
    where status = 'ok' and feed in ('trip_updates', 'vehicle_positions')
    group by service_date, hour_local
),

vehicle_rows as (
    select c.service_date, c.hour_local, count(*) as n_rows
    from {{ ref('stg_vehicle_positions') }} v
    join {{ ref('int_captures') }} c on c.capture_id = v.capture_id
    group by 1, 2
),

trip_update_rows as (
    select c.service_date, c.hour_local, count(*) as n_rows
    from {{ ref('stg_trip_updates') }} t
    join {{ ref('int_captures') }} c on c.capture_id = t.capture_id
    group by 1, 2
),

quarantine_counts as (
    select
        c.service_date,
        c.hour_local,
        count(*) filter (where q.reason = 'invalid_coordinates') as n_invalid_coordinates,
        count(*) filter (where q.reason = 'missing_trip_id') as n_missing_trip_id
    from {{ ref('stg_quarantine') }} q
    join {{ ref('int_captures') }} c on c.capture_id = q.capture_id
    group by 1, 2
),

invalid_coordinate_share as (
    select
        v.service_date,
        v.hour_local,
        'invalid_coordinate_share' as check_name,
        100.0 * coalesce(q.n_invalid_coordinates, 0) / (v.n_rows + coalesce(q.n_invalid_coordinates, 0)) as value
    from vehicle_rows v
    left join quarantine_counts q on q.service_date = v.service_date and q.hour_local = v.hour_local
),

missing_trip_id_share as (
    select
        t.service_date,
        t.hour_local,
        'missing_trip_id_share' as check_name,
        100.0 * coalesce(q.n_missing_trip_id, 0) / (t.n_rows + coalesce(q.n_missing_trip_id, 0)) as value
    from trip_update_rows t
    left join quarantine_counts q on q.service_date = t.service_date and q.hour_local = t.hour_local
),

prediction_coverage as (
    select
        service_date,
        hour_local,
        'prediction_coverage' as check_name,
        100.0 * sum(coverage_numerator) / sum(coverage_denominator) as value
    from {{ ref('agg_line_reliability_hourly') }}
    group by service_date, hour_local
),

schedule_join_coverage as (
    select
        c.service_date,
        c.hour_local,
        'schedule_join_coverage' as check_name,
        100.0 * avg(p.schedule_matched::int) as value
    from {{ ref('fct_stop_predictions') }} p
    join {{ ref('int_captures') }} c on c.capture_id = p.capture_id
    where not p.is_added_trip and not p.is_duplicate_capture
    group by c.service_date, c.hour_local
),

-- a record is late when it was fetched more than 300 seconds after its event
late_event_share as (
    select
        service_date,
        hour_local,
        'late_event_share' as check_name,
        100.0 * avg((epoch(fetched_at) - epoch(event_ts) > 300)::int) as value
    from (
        select c.service_date, c.hour_local, t.fetched_at, t.event_ts
        from {{ ref('stg_trip_updates') }} t
        join {{ ref('int_captures') }} c on c.capture_id = t.capture_id
        where t.event_ts is not null and not c.is_duplicate_capture
        union all
        select c.service_date, c.hour_local, v.fetched_at, v.event_ts
        from {{ ref('stg_vehicle_positions') }} v
        join {{ ref('int_captures') }} c on c.capture_id = v.capture_id
        where v.event_ts is not null and not c.is_duplicate_capture
    )
    group by service_date, hour_local
),

vehicle_counts as (
    select c.service_date, c.hour_local, count(distinct v.vehicle_id) as n_vehicles
    from {{ ref('stg_vehicle_positions') }} v
    join {{ ref('int_captures') }} c on c.capture_id = v.capture_id
    group by 1, 2
),

vehicle_count_drop as (
    select
        cur.service_date,
        cur.hour_local,
        'vehicle_count_drop' as check_name,
        case
            when base.n_vehicles > 0
                then 100.0 * (base.n_vehicles - cur.n_vehicles) / base.n_vehicles
        end as value
    from vehicle_counts cur
    left join vehicle_counts base
        on base.service_date = cur.service_date - 7 and base.hour_local = cur.hour_local
),

all_checks as (
    select * from missed_runs
    union all select * from duplicate_share
    union all select * from invalid_coordinate_share
    union all select * from missing_trip_id_share
    union all select * from prediction_coverage
    union all select * from schedule_join_coverage
    union all select * from late_event_share
    union all select * from vehicle_count_drop
)

select
    a.service_date,
    a.hour_local,
    a.check_name,
    a.value,
    t.direction,
    t.warn as warn_threshold,
    t.fail as fail_threshold,
    case
        when a.value is null and a.check_name = 'vehicle_count_drop' then 'no_baseline'
        when a.value is null then 'no_data'
        when t.direction = 'above' and a.value >= t.fail then 'fail'
        when t.direction = 'above' and a.value >= t.warn then 'warn'
        when t.direction = 'below' and a.value < t.fail then 'fail'
        when t.direction = 'below' and a.value < t.warn then 'warn'
        else 'ok'
    end as status
from all_checks a
join {{ ref('quality_thresholds') }} t on t.check_name = a.check_name
join hours h on h.service_date = a.service_date and h.hour_local = a.hour_local
