-- Line hours with at least one active line-scope service-disruption alert (see agg_active_alerts).
-- An alert that covers a single station is not here: it flags that station only (int_stop_alert_flags).
select distinct line, service_date, hour_local
from {{ ref('agg_active_alerts') }}
where is_service_disruption and scope = 'line'
