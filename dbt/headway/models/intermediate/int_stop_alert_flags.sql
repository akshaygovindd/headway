-- Stations under a stop-level service-disruption alert, per line and hour: a closed station, a
-- skipped stop. They flag only that station's trip stops (excluded from arrival coverage, shown
-- as information on the line); the line itself is not disrupted (see int_alert_scopes).
select distinct line, service_date, hour_local, unnest(station_ids) as station_id
from {{ ref('int_alert_scopes') }}
where is_service_disruption and scope = 'stop'
