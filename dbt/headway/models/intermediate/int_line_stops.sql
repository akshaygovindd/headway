-- Which stops (and their stations) each line serves, from the static GTFS route -> trip -> stop
-- times. Every feed version is kept, so a stop dropped by a later feed still belongs to its line for
-- data captured earlier. The reference for the line-membership data tests (DATA_QUALITY.md).
select distinct
    t.route_id as line,
    st.stop_id,
    coalesce(nullif(s.parent_station, ''), st.stop_id) as station_id
from {{ ref('stg_gtfs__stop_times') }} st
join {{ ref('stg_gtfs__trips') }} t on t.feed_version = st.feed_version and t.trip_id = st.trip_id
left join {{ ref('dim_stop') }} s on s.stop_id = st.stop_id
