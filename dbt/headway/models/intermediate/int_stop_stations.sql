-- The station of each stop: its parent station when it has one, else the stop itself, so the
-- platforms of one station count once. The name is the station's own name.
select
    s.stop_id,
    coalesce(nullif(s.parent_station, ''), s.stop_id) as station_id,
    coalesce(p.stop_name, s.stop_name) as station_name
from {{ ref('dim_stop') }} s
left join {{ ref('dim_stop') }} p on p.stop_id = nullif(s.parent_station, '')
