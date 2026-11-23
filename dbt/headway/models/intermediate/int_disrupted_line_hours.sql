-- Line hours with at least one active service-disruption alert (see agg_active_alerts).
select distinct line, service_date, hour_local
from {{ ref('agg_active_alerts') }}
where is_service_disruption
