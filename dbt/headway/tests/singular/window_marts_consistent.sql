{{ config(tags=['r2'], severity='error') }}
-- Window marts cover exactly the stated window (METRICS.md "Windows are exact"): only the labels
-- last_24h and last_7d, never more hours than the window holds, last_24h inside last_7d, and counts
-- that equal the sum over the window's hours of the hourly mart. A window can hold fewer hours than
-- its length (no service overnight, data outages), never more.
-- agg_actual_deviation_hourly leaves out stops under a stop-level alert (n_stop_excluded), which the
-- window does not, so its window is only required to be at least the hourly sum, and equal for a
-- line with no stop-level flag in the window.
with labels as (
    {%- set window_models = [
        ref('agg_line_reliability_window'), ref('agg_actual_deviation_window'), ref('agg_headway_window'),
        ref('agg_ett_window'), ref('agg_accuracy_window'), ref('agg_countdown_accuracy_window')
    ] %}
    {%- for m in window_models %}
    select '{{ m.identifier }}' as model, window_label as label from {{ m }}{% if not loop.last %} union all {% endif %}
    {%- endfor %}
),

bad_labels as (
    select model, 'unexpected window_label ' || coalesce(label, 'null') as problem
    from labels
    where label is null or label not in ('last_24h', 'last_7d')
),

hours as (
    select
        window_label,
        count(distinct hour_start) as n_hours,
        case window_label when 'last_24h' then 24 else 168 end as max_hours
    from {{ ref('int_window_hours') }}
    group by 1
),

too_many_hours as (
    select 'int_window_hours' as model, window_label || ' holds ' || n_hours || ' hours, more than ' || max_hours as problem
    from hours
    where n_hours > max_hours
),

day_not_in_week as (
    select 'int_window_hours' as model, 'last_24h hour ' || h.hour_start || ' is not in last_7d' as problem
    from {{ ref('int_window_hours') }} h
    where h.window_label = 'last_24h'
        and not exists (
            select 1 from {{ ref('int_window_hours') }} w
            where w.window_label = 'last_7d' and w.hour_start = h.hour_start
        )
),

nested as (
    select 'agg_actual_deviation_window' as model, 'last_24h n_arrivals above last_7d for ' || a.line as problem
    from {{ ref('agg_actual_deviation_window') }} a
    join {{ ref('agg_actual_deviation_window') }} b on b.line = a.line and b.window_label = 'last_7d'
    where a.window_label = 'last_24h' and a.n_arrivals > b.n_arrivals
    union all
    select 'agg_headway_window', 'last_24h n_headways above last_7d for ' || a.line
    from {{ ref('agg_headway_window') }} a
    join {{ ref('agg_headway_window') }} b on b.line = a.line and b.window_label = 'last_7d'
    where a.window_label = 'last_24h' and a.n_headways > b.n_headways
    union all
    select 'agg_line_reliability_window', 'last_24h n_eligible above last_7d for ' || a.line
    from {{ ref('agg_line_reliability_window') }} a
    join {{ ref('agg_line_reliability_window') }} b on b.line = a.line and b.window_label = 'last_7d'
    where a.window_label = 'last_24h' and a.n_eligible > b.n_eligible
),

headway_hourly as (
    select
        w.window_label,
        h.line,
        sum(h.n_headways) as n_headways,
        sum(h.coverage_numerator) as coverage_numerator,
        sum(h.coverage_denominator) filter (where not h.data_outage) as coverage_denominator
    from {{ ref('agg_headway_adherence_hourly') }} h
    join {{ ref('int_window_hours') }} w on w.service_date = h.service_date and w.hour_local = h.hour_local
    group by 1, 2
),

headway_sum as (
    select 'agg_headway_window' as model, 'window differs from the sum of its hours for ' || w.window_label || ' ' || w.line as problem
    from {{ ref('agg_headway_window') }} w
    left join headway_hourly h on h.window_label = w.window_label and h.line = w.line
    where w.n_headways <> coalesce(h.n_headways, 0)
        or w.coverage_numerator <> coalesce(h.coverage_numerator, 0)
        or w.coverage_denominator is distinct from h.coverage_denominator
),

reliability_hourly as (
    select
        w.window_label,
        h.line,
        sum(h.n_eligible) as n_eligible,
        sum(h.coverage_numerator) as coverage_numerator
    from {{ ref('agg_line_reliability_hourly') }} h
    join {{ ref('int_window_hours') }} w on w.service_date = h.service_date and w.hour_local = h.hour_local
    group by 1, 2
),

reliability_sum as (
    select 'agg_line_reliability_window' as model, 'window differs from the sum of its hours for ' || w.window_label || ' ' || w.line as problem
    from {{ ref('agg_line_reliability_window') }} w
    left join reliability_hourly h on h.window_label = w.window_label and h.line = w.line
    where w.n_eligible <> coalesce(h.n_eligible, 0) or w.coverage_numerator <> coalesce(h.coverage_numerator, 0)
),

deviation_hourly as (
    select w.window_label, h.line, sum(h.n_arrivals) as n_arrivals
    from {{ ref('agg_actual_deviation_hourly') }} h
    join {{ ref('int_window_hours') }} w on w.service_date = h.service_date and w.hour_local = h.hour_local
    group by 1, 2
),

stop_flagged as (
    select distinct w.window_label, f.line
    from {{ ref('int_stop_alert_flags') }} f
    join {{ ref('int_window_hours') }} w on w.service_date = f.service_date and w.hour_local = f.hour_local
),

deviation_sum as (
    select 'agg_actual_deviation_window' as model, 'window n_arrivals below its hours for ' || w.window_label || ' ' || w.line as problem
    from {{ ref('agg_actual_deviation_window') }} w
    left join deviation_hourly h on h.window_label = w.window_label and h.line = w.line
    left join stop_flagged s on s.window_label = w.window_label and s.line = w.line
    where w.n_arrivals < coalesce(h.n_arrivals, 0)
        or (s.line is null and w.n_arrivals <> coalesce(h.n_arrivals, 0))
)

select * from bad_labels
union all select * from too_many_hours
union all select * from day_not_in_week
union all select * from nested
union all select * from headway_sum
union all select * from reliability_sum
union all select * from deviation_sum
