-- Incident list for the Incidents page (Release 3): the incident, its evidence summary, the
-- thresholds it crossed and its match status against alerts. summary_text, template_used,
-- number_check, rejection_reason, llm_model, summary_generated_at and summary_evidence_hash are
-- null here and filled by the transform (headway_transform/incident_summaries.py) once the build
-- is done, the way agg_forecast_next_hour gets its model forecast. summary_stale is set there when
-- the stored summary describes an earlier version of an open incident.
select
    i.incident_id,
    i.evidence_hash,
    i.line,
    i.direction_id,
    i.incident_start_ts,
    i.incident_end_ts,
    i.n_hours,
    i.is_open,
    i.thresholds_crossed,
    i.min_coverage_pct,
    i.mean_coverage_pct,
    i.n_outage_hours_inside,
    i.evidence_json,
    m.match_status,
    m.alert_lag_s,
    m.first_alert_id,
    m.counterpart_ids as matched_alert_ids,
    cast(null as varchar) as summary_text,
    cast(null as boolean) as template_used,
    cast(null as varchar) as number_check,
    cast(null as varchar) as rejection_reason,
    cast(null as varchar) as llm_model,
    cast(null as timestamp with time zone) as summary_generated_at,
    cast(null as varchar) as summary_evidence_hash,
    cast(null as boolean) as summary_stale
from {{ ref('fct_incidents') }} i
left join {{ ref('int_alert_matching') }} m on m.kind = 'incident' and m.item_id = i.incident_id
