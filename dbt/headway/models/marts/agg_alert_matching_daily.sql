-- Incidents and line-scope service-impacting alerts per line and service date, and how many have a
-- counterpart (int_alert_matching). An incident is dated by its start, an alert by its first capture.
-- Incident and alert counts are separate populations; "unmatched" says only that no counterpart
-- was found within the match window, not that either side was wrong. median_alert_lag_s is over
-- matched incidents: seconds from incident start to the first matching alert's first capture.
select
    line,
    service_date,
    count(*) filter (where kind = 'incident') as n_incidents,
    count(*) filter (where match_status = 'matched' and kind = 'incident') as n_incidents_matched,
    count(*) filter (where match_status = 'unmatched_incident') as n_incidents_unmatched,
    count(*) filter (where kind = 'alert') as n_alerts,
    count(*) filter (where match_status = 'matched' and kind = 'alert') as n_alerts_matched,
    count(*) filter (where match_status = 'unmatched_alert') as n_alerts_unmatched,
    median(alert_lag_s) filter (where kind = 'incident' and match_status = 'matched') as median_alert_lag_s,
    count(alert_lag_s) filter (where kind = 'incident') as n_with_lag
from {{ ref('int_alert_matching') }}
group by line, service_date
