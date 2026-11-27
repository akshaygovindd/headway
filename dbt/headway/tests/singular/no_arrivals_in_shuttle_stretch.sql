{{ config(tags=['r2'], severity='error') }}
-- No reconstructed arrival at a station inside an active line-scope shuttle or detour stretch, for
-- an hour in which the alert named that station. A train cannot arrive where shuttle buses replace
-- service. Delay and reduced-service alerts are left out: trains still run there.
select
    r.line,
    r.service_date,
    a.hour_local,
    a.alert_id,
    st.station_id,
    count(*) as n_arrivals
from {{ ref('fct_reconstructed_arrivals') }} r
join {{ ref('int_stop_stations') }} st on st.stop_id = r.stop_id
join {{ ref('int_alert_scopes') }} a
    on a.line = r.line
    and a.service_date = r.service_date
    and a.hour_local = hour(r.arrival_ts at time zone '{{ var("timezone") }}')
    and list_contains(a.station_ids, st.station_id)
where a.scope = 'line'
    and a.is_service_disruption
    and (a.effect in ('DETOUR', 'NO_SERVICE') or regexp_matches(coalesce(a.header_text, ''), 'shuttle|suspen', 'i'))
group by 1, 2, 3, 4, 5
