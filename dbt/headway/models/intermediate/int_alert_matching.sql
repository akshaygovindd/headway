-- Incidents against line-scope service-impacting alerts (Release 3, "alert vs reality", C13).
-- An incident and an alert match when they are on the same line and their time ranges overlap once
-- widened by incidents.match_window_min minutes either way. The incident's range is its first
-- breach hour to the end of its last; an alert's range is from the first to the last capture in
-- which it was active (this build's captures: an alert already active before the lookback window
-- starts at its first capture in it). Alerts are line-scope with the service-disruption effect rule
-- of Release 2 (agg_active_alerts). One row per incident (kind 'incident') and per alert (kind
-- 'alert'). alert_lag_s is the first matching alert's first capture minus the incident start, in
-- seconds: positive when the alert came later, negative when it came first. Statuses never say
-- an incident or an alert was "missed" or "phantom": they describe which side has a counterpart.
{% set win = var('incidents')['match_window_min'] %}
with alerts as (
    select
        line,
        alert_id,
        arg_max(effect, (last_seen, service_date, hour_local)) as effect,
        arg_max(header_text, (last_seen, service_date, hour_local)) as header_text,
        min(first_seen) as first_seen,
        max(last_seen) as last_seen
    from {{ ref('int_alert_scopes') }}
    where scope = 'line' and is_service_disruption
    group by line, alert_id
),

pairs as (
    select
        i.incident_id, a.line, a.alert_id, a.header_text, a.first_seen,
        epoch(a.first_seen) - epoch(i.incident_start_ts) as alert_lag_s
    from {{ ref('fct_incidents') }} i
    join alerts a
        on a.line = i.line
        and a.first_seen <= i.incident_end_ts + interval {{ win }} minute
        and a.last_seen >= i.incident_start_ts - interval {{ win }} minute
),

incident_side as (
    select
        'incident' as kind,
        i.incident_id as item_id,
        i.line,
        i.incident_start_ts as event_ts,
        {{ service_date_naive('i.incident_start') }} as service_date,
        count(p.alert_id) as n_counterparts,
        case when count(p.alert_id) > 0 then 'matched' else 'unmatched_incident' end as match_status,
        arg_min(p.alert_lag_s, (p.first_seen, p.alert_id)) as alert_lag_s,
        arg_min(p.alert_id, (p.first_seen, p.alert_id)) as first_alert_id,
        string_agg(distinct p.alert_id, ',' order by p.alert_id) as counterpart_ids
    from {{ ref('fct_incidents') }} i
    left join pairs p on p.incident_id = i.incident_id
    group by i.incident_id, i.line, i.incident_start_ts, i.incident_start
),

alert_side as (
    select
        'alert' as kind,
        a.alert_id as item_id,
        a.line,
        a.first_seen as event_ts,
        {{ service_date_naive("(a.first_seen at time zone '" ~ var('timezone') ~ "')") }} as service_date,
        count(p.incident_id) as n_counterparts,
        case when count(p.incident_id) > 0 then 'matched' else 'unmatched_alert' end as match_status,
        cast(null as double) as alert_lag_s,
        cast(null as varchar) as first_alert_id,
        string_agg(distinct p.incident_id, ',' order by p.incident_id) as counterpart_ids
    from alerts a
    left join pairs p on p.alert_id = a.alert_id and p.line = a.line
    group by a.alert_id, a.line, a.first_seen
)

select * from incident_side
union all
select * from alert_side
