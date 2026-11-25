-- Headway adherence per segment over the last 24 hours and the last 7 days (int_window_hours), for
-- the segment health strip of a line page (Release 4). Same headway rules and bands as
-- agg_headway_window (within 0.5 to 1.5 of the scheduled headway, bunched below, a gap above).
--
-- Segment: two neighbouring stops of a scheduled trip, from_stop (the stop before) to to_stop. A
-- headway belongs to the segment that ends at the stop where it is measured (fct_headways is one
-- row per later train and stop), so the first stop of a trip has no segment. Both stops are
-- platform stop ids of one direction; names are the station names. segment_order ranks the
-- segments of a line and direction by the typical stop_sequence of their to_stop, so branches
-- share positions and a consumer draws each branch from its own segments.
--
-- Coverage is valid headways over scheduled headways of the segment (scheduled stop times with a
-- predecessor at the to_stop) in non-outage hours. Outage hours are left out and counted in
-- n_excluded_outage. disrupted is true when, in any non-outage hour of the window, a line-scope
-- service-disruption alert covered the station of either stop (int_alert_scopes.station_ids);
-- n_disruption counts valid headways of such hours. health buckets pct_within_band: even at 80 or
-- more, some_bunching at 60 or more, else bunched_gaps (null without valid headways). Percentages
-- are 0-100.
with stops as (
    select
        s.line,
        s.direction_id,
        s.service_date,
        s.hour_local,
        s.trip_instance_key,
        s.stop_sequence,
        s.stop_id as to_stop_id,
        lag(s.stop_id) over trip as from_stop_id,
        lag(s.scheduled_arrival_ts) over stop as previous_ts
    from {{ ref('int_scheduled_stop_times') }} s
    window
        trip as (partition by s.trip_instance_key order by s.stop_sequence),
        stop as (
            partition by s.line, s.direction_id, s.service_date, s.stop_id
            order by s.scheduled_arrival_ts, s.trip_instance_key
        )
),

disrupted_stations as (
    select distinct line, service_date, hour_local, unnest(station_ids) as station_id
    from {{ ref('int_alert_scopes') }}
    where scope = 'line' and is_service_disruption
),

-- scheduled headways per segment and hour of the later train
scheduled_hours as (
    select
        s.line, s.direction_id, s.from_stop_id, s.to_stop_id, s.service_date, s.hour_local,
        count(*) as scheduled_headways,
        avg(s.stop_sequence) as avg_stop_sequence
    from stops s
    where s.previous_ts is not null and s.from_stop_id is not null
    group by 1, 2, 3, 4, 5, 6
),

segments as (
    select
        line, direction_id, from_stop_id, to_stop_id,
        row_number() over (
            partition by line, direction_id
            order by avg(avg_stop_sequence), to_stop_id, from_stop_id
        ) as segment_order
    from scheduled_hours
    group by 1, 2, 3, 4
),

scheduled as (
    select
        s.line, s.direction_id, s.from_stop_id, s.to_stop_id, w.window_label,
        sum(s.scheduled_headways) filter (where not f.data_outage) as scheduled_headways,
        coalesce(bool_or(coalesce(dis.hit, false)) filter (where not f.data_outage), false) as disrupted
    from scheduled_hours s
    join {{ ref('int_window_hours') }} w on w.service_date = s.service_date and w.hour_local = s.hour_local
    join {{ ref('int_hour_flags') }} f on f.service_date = s.service_date and f.hour_local = s.hour_local
    left join {{ ref('int_stop_stations') }} fss on fss.stop_id = s.from_stop_id
    left join {{ ref('int_stop_stations') }} tss on tss.stop_id = s.to_stop_id
    left join lateral (
        select true as hit
        from disrupted_stations d
        where d.line = s.line and d.service_date = s.service_date and d.hour_local = s.hour_local
            and d.station_id in (fss.station_id, tss.station_id)
        limit 1
    ) dis on true
    group by 1, 2, 3, 4, 5
),

-- each candidate headway, with the stop before its to_stop in the later train's trip
candidates as (
    select
        h.line, h.direction_id, h.stop_id as to_stop_id, st.from_stop_id, h.service_date, h.hour_local,
        h.status, h.band, h.ratio
    from {{ ref('fct_headways') }} h
    join stops st
        on st.trip_instance_key = h.to_trip_instance_key and st.stop_sequence = h.stop_sequence
    where st.from_stop_id is not null
),

headways as (
    select
        c.line, c.direction_id, c.from_stop_id, c.to_stop_id, w.window_label,
        count(*) filter (where not f.data_outage and c.status = 'valid') as n_headways,
        count(*) filter (where not f.data_outage and c.status = 'valid' and dis.hit) as n_disruption,
        count(*) filter (where not f.data_outage and c.band = 'within') as n_within,
        count(*) filter (where not f.data_outage and c.band = 'bunched') as n_bunched,
        count(*) filter (where not f.data_outage and c.band = 'gap') as n_gap,
        count(*) filter (where f.data_outage) as n_excluded_outage
    from candidates c
    join {{ ref('int_window_hours') }} w on w.service_date = c.service_date and w.hour_local = c.hour_local
    join {{ ref('int_hour_flags') }} f on f.service_date = c.service_date and f.hour_local = c.hour_local
    left join {{ ref('int_stop_stations') }} fss on fss.stop_id = c.from_stop_id
    left join {{ ref('int_stop_stations') }} tss on tss.stop_id = c.to_stop_id
    left join lateral (
        select true as hit
        from disrupted_stations d
        where d.line = c.line and d.service_date = c.service_date and d.hour_local = c.hour_local
            and d.station_id in (fss.station_id, tss.station_id)
        limit 1
    ) dis on true
    group by 1, 2, 3, 4, 5
),

result as (
    select
        s.line,
        s.direction_id,
        s.from_stop_id,
        s.to_stop_id,
        s.window_label,
        fs.station_name as from_stop_name,
        ts.station_name as to_stop_name,
        g.segment_order,
        coalesce(h.n_headways, 0) as n_headways,
        100.0 * h.n_within / nullif(h.n_headways, 0) as pct_within_band,
        100.0 * h.n_bunched / nullif(h.n_headways, 0) as pct_bunched,
        100.0 * h.n_gap / nullif(h.n_headways, 0) as pct_gap,
        coalesce(h.n_excluded_outage, 0) as n_excluded_outage,
        coalesce(h.n_disruption, 0) as n_disruption,
        s.disrupted,
        s.scheduled_headways,
        coalesce(h.n_headways, 0) as coverage_numerator,
        s.scheduled_headways as coverage_denominator,
        100.0 * coalesce(h.n_headways, 0) / nullif(s.scheduled_headways, 0) as coverage_pct
    from scheduled s
    join segments g
        on g.line = s.line and g.direction_id = s.direction_id
        and g.from_stop_id = s.from_stop_id and g.to_stop_id = s.to_stop_id
    left join headways h
        on h.line = s.line and h.direction_id = s.direction_id and h.from_stop_id = s.from_stop_id
        and h.to_stop_id = s.to_stop_id and h.window_label = s.window_label
    left join {{ ref('int_stop_stations') }} fs on fs.stop_id = s.from_stop_id
    left join {{ ref('int_stop_stations') }} ts on ts.stop_id = s.to_stop_id
)

select
    *,
    case
        when pct_within_band is null then null
        when pct_within_band >= 80 then 'even'
        when pct_within_band >= 60 then 'some_bunching'
        else 'bunched_gaps'
    end as health
from result
