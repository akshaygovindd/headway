-- Descriptive comparison of on-time and headway shares on snow, heavy-rain and extreme-heat days
-- against the other days of the same window (Release 3, ADR-022, docs/METRICS.md). Nothing here says
-- the weather caused a difference: it compares two groups of days.
--
-- Window: every service date in agg_actual_deviation_daily and agg_headway_adherence_daily except the
-- latest one, which may still be open. A day counts once, all lines and directions pooled: on_time is
-- the share of usable arrivals within -60 to +300 s of the timetable, headway_within_band the share of
-- valid headways within 0.5 to 1.5 times the scheduled one. Days with no weather row, or a null flag
-- for the condition, are left out of that condition; a day of another condition counts as "other".
-- Each value is a pooled share over the days of its group, with a 95 % percentile interval from
-- bootstrap_reps resamples of whole days (seeded by a hash, so a rebuild gives the same numbers).
-- Withheld (values null, withheld_reason set) when either group has fewer than min_days days.
{% set reps = 1000 %}
{% set min_days = 5 %}
{% set seed = 'weather-2026' %}
with day_metrics as (
    select service_date, 'on_time' as metric, sum(pct_on_time_window * n_arrivals / 100.0) as num, sum(n_arrivals) as den
    from {{ ref('agg_actual_deviation_daily') }}
    where pct_on_time_window is not null
      and service_date < (select max(service_date) from {{ ref('agg_actual_deviation_daily') }})
    group by service_date
    union all
    select service_date, 'headway_within_band' as metric, sum(n_within) as num, sum(n_headways) as den
    from (
        select service_date, n_headways, round(pct_within_band * n_headways / 100.0) as n_within
        from {{ ref('agg_headway_adherence_daily') }}
        where pct_within_band is not null
          and service_date < (select max(service_date) from {{ ref('agg_headway_adherence_daily') }})
    )
    group by service_date
),

conditions as (
    select * from (values ('snow'), ('heavy_rain'), ('extreme_heat')) as t (condition)
),

metrics as (
    select * from (values ('on_time'), ('headway_within_band')) as t (metric)
),

tagged as (
    select
        c.condition,
        m.metric,
        m.service_date,
        m.num,
        m.den,
        case c.condition
            when 'snow' then w.snow_flag
            when 'heavy_rain' then w.heavy_rain_flag
            else w.extreme_heat_flag
        end as flagged
    from day_metrics m
    join {{ ref('stg_weather_daily') }} w on w.weather_date = m.service_date
    cross join conditions c
    where m.den > 0
),

ranked as (
    select
        condition,
        metric,
        case when flagged then 'condition' else 'other' end as grp,
        num,
        den,
        service_date,
        row_number() over (
            partition by condition, metric, flagged order by service_date
        ) as pos,
        count(*) over (partition by condition, metric, flagged) as k
    from tagged
    where flagged is not null
),

groups as (
    select condition, metric, grp, any_value(k) as k, min(service_date) as first_day, max(service_date) as last_day,
        sum(num) as num, sum(den) as den
    from ranked
    group by condition, metric, grp
),

draws as (
    select
        g.condition,
        g.metric,
        g.grp,
        r.rep,
        1 + (hash('{{ seed }}' || g.condition || g.metric || g.grp || cast(r.rep as varchar) || cast(j.j as varchar)) % g.k) as pos
    from groups g
    cross join range(1, {{ reps }} + 1) as r (rep)
    join range(1, (select coalesce(max(k), 0) + 1 from groups)) as j (j) on j.j <= g.k
),

replicates as (
    select d.condition, d.metric, d.grp, d.rep, 100.0 * sum(x.num) / sum(x.den) as share
    from draws d
    join ranked x on x.condition = d.condition and x.metric = d.metric and x.grp = d.grp and x.pos = d.pos
    group by d.condition, d.metric, d.grp, d.rep
),

intervals as (
    select
        condition,
        metric,
        grp,
        quantile_cont(share, 0.025) as ci_lo,
        quantile_cont(share, 0.975) as ci_hi
    from replicates
    group by condition, metric, grp
),

summary as (
    select
        g.condition,
        g.metric,
        g.grp,
        g.k as n_days,
        100.0 * g.num / g.den as value,
        i.ci_lo,
        i.ci_hi,
        g.first_day,
        g.last_day
    from groups g
    join intervals i on i.condition = g.condition and i.metric = g.metric and i.grp = g.grp
),

base as (
    select c.condition, m.metric from conditions c cross join metrics m
),

joined as (
    select
        b.condition,
        b.metric,
        coalesce(c.n_days, 0) as n_days_condition,
        coalesce(o.n_days, 0) as n_days_other,
        c.value as value_condition,
        o.value as value_other,
        c.ci_lo as ci_lo_condition,
        c.ci_hi as ci_hi_condition,
        o.ci_lo as ci_lo_other,
        o.ci_hi as ci_hi_other,
        least(c.first_day, o.first_day) as window_start,
        greatest(c.last_day, o.last_day) as window_end
    from base b
    left join summary c on c.condition = b.condition and c.metric = b.metric and c.grp = 'condition'
    left join summary o on o.condition = b.condition and o.metric = b.metric and o.grp = 'other'
)

select
    condition,
    metric,
    n_days_condition,
    n_days_other,
    n_days_condition < {{ min_days }} or n_days_other < {{ min_days }} as withheld,
    case
        when n_days_condition < {{ min_days }} and n_days_other < {{ min_days }} then 'fewer than {{ min_days }} days of either kind'
        when n_days_condition < {{ min_days }} then 'fewer than {{ min_days }} ' || replace(condition, '_', '-') || ' days'
        when n_days_other < {{ min_days }} then 'fewer than {{ min_days }} other days'
    end as withheld_reason,
    case when n_days_condition >= {{ min_days }} and n_days_other >= {{ min_days }} then value_condition end as value_condition,
    case when n_days_condition >= {{ min_days }} and n_days_other >= {{ min_days }} then value_other end as value_other,
    case when n_days_condition >= {{ min_days }} and n_days_other >= {{ min_days }} then ci_lo_condition end as ci_lo_condition,
    case when n_days_condition >= {{ min_days }} and n_days_other >= {{ min_days }} then ci_hi_condition end as ci_hi_condition,
    case when n_days_condition >= {{ min_days }} and n_days_other >= {{ min_days }} then ci_lo_other end as ci_lo_other,
    case when n_days_condition >= {{ min_days }} and n_days_other >= {{ min_days }} then ci_hi_other end as ci_hi_other,
    window_start,
    window_end,
    {{ reps }} as bootstrap_reps,
    {{ min_days }} as min_days
from joined
