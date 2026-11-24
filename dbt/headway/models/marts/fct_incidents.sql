-- Incidents per line, direction and incident_start (Release 3, docs/METRICS.md "Incidents"). An
-- incident opens when the hours of int_incident_hours breach for incidents.min_hours consecutive
-- hours and closes after one hour with no breach. Consecutive means one hour apart on the
-- timeline of evaluated hours: a data-outage hour is not evaluated, so it ends the run of
-- breaches (an outage never opens an incident) and is counted in n_outage_hours_inside.
-- is_open: no later evidence closes it yet (the next hour is missing or a data outage and
-- nothing later has been observed). incident_end is the end of the last breach hour.
-- evidence_json carries every value a summary may use: hours with their values and rules crossed,
-- baselines, thresholds, alert ids and headers, coverage, the baseline rule's state. evidence_hash is
-- the incident version: it changes when the evidence does.
{% set cfg = var('incidents') %}
with evaluated as (
    select * from {{ ref('int_incident_hours') }} where not data_outage
),

breaches as (
    select
        *,
        cast(epoch(hour_start) / 3600 as bigint)
            - row_number() over (partition by line, direction_id order by hour_start) as island
    from evaluated
    where breach
),

islands as (
    select
        line, direction_id, island,
        min(hour_start) as incident_start,
        max(hour_start) + interval 1 hour as incident_end,
        count(*) as n_hours,
        min(coverage_pct) as min_coverage_pct,
        avg(coverage_pct) as mean_coverage_pct,
        bool_or(crossed_deviation) as crossed_deviation,
        bool_or(crossed_gap) as crossed_gap,
        bool_or(crossed_skipped) as crossed_skipped,
        bool_or(crossed_alert) as crossed_alert,
        bool_or(not baseline_enabled) as baseline_rule_off_in_some_hours,
        max(baseline_days) as baseline_days,
        to_json(list(struct_pack(
            hour := strftime(hour_start, '%H:%M'),
            date := strftime(hour_start, '%Y-%m-%d'),
            median_deviation_s := round(median_deviation_s, 1),
            baseline_median_s := round(baseline_median_s, 1),
            deviation_over_baseline_s := round(median_deviation_s - baseline_median_s, 1),
            gap_share_pct := round(pct_gap, 1),
            skipped_share_pct := round(skipped_pct, 1),
            coverage_pct := round(coverage_pct, 1),
            alert_active := alert_active,
            crossed := list_filter([
                case when crossed_deviation then 'deviation' end,
                case when crossed_gap then 'gap' end,
                case when crossed_skipped then 'skipped' end,
                case when crossed_alert then 'alert' end
            ], x -> x is not null)
        ) order by hour_start)) as hours_json
    from breaches
    group by line, direction_id, island
    having count(*) >= {{ cfg['min_hours'] }}
),

latest as (
    select line, direction_id, max(hour_start) as latest_hour_start
    from evaluated
    group by 1, 2
),

closing as (
    select
        i.*,
        l.latest_hour_start,
        -- open until a later hour was evaluated: the clear hour itself, or any hour after a gap
        not (l.latest_hour_start >= i.incident_end) as is_open
    from islands i
    join latest l on l.line = i.line and l.direction_id is not distinct from i.direction_id
),

outage_inside as (
    select i.line, i.direction_id, i.island, count(h.line) as n_outage_hours_inside
    from islands i
    left join {{ ref('int_incident_hours') }} h
        on h.line = i.line and h.direction_id is not distinct from i.direction_id
        and h.data_outage and h.hour_start >= i.incident_start and h.hour_start < i.incident_end
    group by 1, 2, 3
),

alerts as (
    select
        i.line, i.direction_id, i.island,
        to_json(coalesce(
            list(distinct struct_pack(alert_id := s.alert_id, effect := s.effect, header := s.header_text))
                filter (where s.alert_id is not null),
            []
        )) as alerts_json
    from islands i
    left join {{ ref('int_alert_scopes') }} s
        on s.line = i.line and s.scope = 'line' and s.is_service_disruption
        and cast(s.service_date as timestamp)
            + to_hours(s.hour_local + case when s.hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
            >= i.incident_start
        and cast(s.service_date as timestamp)
            + to_hours(s.hour_local + case when s.hour_local < {{ var('service_day_cutoff_hour') }} then 24 else 0 end)
            < i.incident_end
    group by 1, 2, 3
),

assembled as (
    select
        c.line,
        c.direction_id,
        c.incident_start,
        c.incident_end,
        c.n_hours,
        c.is_open,
        c.crossed_deviation, c.crossed_gap, c.crossed_skipped, c.crossed_alert,
        c.min_coverage_pct,
        c.mean_coverage_pct,
        o.n_outage_hours_inside,
        c.incident_start at time zone '{{ var("timezone") }}' as incident_start_ts,
        c.incident_end at time zone '{{ var("timezone") }}' as incident_end_ts,
        array_to_string(list_filter([
            case when c.crossed_deviation then 'deviation' end,
            case when c.crossed_gap then 'gap' end,
            case when c.crossed_skipped then 'skipped' end,
            case when c.crossed_alert then 'alert' end
        ], x -> x is not null), ',') as thresholds_crossed,
        cast(json_object(
            'line', c.line,
            'direction_id', c.direction_id,
            'date', strftime(c.incident_start, '%Y-%m-%d'),
            'start', strftime(c.incident_start, '%H:%M'),
            'end', strftime(c.incident_end, '%H:%M'),
            'is_open', c.is_open,
            'n_hours', c.n_hours,
            'hours', c.hours_json,
            'thresholds', json_object(
                'deviation_over_baseline_s', {{ cfg['deviation_over_baseline_s'] }},
                'gap_share_pct', {{ cfg['gap_share_pct'] }},
                'skipped_share_pct', {{ cfg['skipped_share_pct'] }},
                'min_hours', {{ cfg['min_hours'] }}
            ),
            'baseline_rule', case
                when c.baseline_rule_off_in_some_hours
                    then 'disabled in some hours: fewer than {{ cfg['min_baseline_days'] }} earlier service days of this day type'
                else 'enabled' end,
            'baseline_days', c.baseline_days,
            'min_coverage_pct', round(c.min_coverage_pct, 1),
            'mean_coverage_pct', round(c.mean_coverage_pct, 1),
            'data_outage_hours_inside', o.n_outage_hours_inside,
            'alerts', a.alerts_json
        ) as varchar) as evidence_json
    from closing c
    join outage_inside o on o.line = c.line and o.direction_id is not distinct from c.direction_id and o.island = c.island
    join alerts a on a.line = c.line and a.direction_id is not distinct from c.direction_id and a.island = c.island
)

select
    md5(line || '|' || coalesce(cast(direction_id as varchar), '') || '|' || cast(incident_start as varchar)) as incident_id,
    md5(evidence_json) as evidence_hash,
    {{ service_date_naive('incident_start') }} as incident_date,
    *
from assembled
