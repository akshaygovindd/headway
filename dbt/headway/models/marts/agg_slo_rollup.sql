-- Service level objectives over the last 7 and the last 30 service dates ending at the latest date
-- of agg_slo_daily (closed days come from history, so 30 days exist once history has built up).
-- Events are summed over the days, so the share and the error budget are those of the whole
-- window, not an average of daily shares. days_covered is how many dates of the window have events.
with latest as (
    select max(service_date) as last_date from {{ ref('agg_slo_daily') }}
),

windows as (
    select * from (values ('last_7d', 7), ('last_30d', 30)) as w (window_label, n_days)
)

select
    d.slo_name,
    w.window_label,
    any_value(d.target_pct) as target_pct,
    sum(d.good_events) as good_events,
    sum(d.total_events) as total_events,
    100.0 * sum(d.good_events) / nullif(sum(d.total_events), 0) as actual_pct,
    100.0 * sum(d.good_events) / nullif(sum(d.total_events), 0) >= any_value(d.target_pct) as met,
    100.0 * (sum(d.total_events) - sum(d.good_events))
        / nullif((100.0 - any_value(d.target_pct)) * sum(d.total_events), 0) as error_budget_used,
    count(distinct d.service_date) as days_covered,
    any_value(d.counts_toward_slo) as counts_toward_slo
from {{ ref('agg_slo_daily') }} d
cross join latest l
cross join windows w
where d.service_date > l.last_date - w.n_days
group by d.slo_name, w.window_label
