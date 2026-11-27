-- Travel time between two stations of a line, per direction, time band and window, for the
-- "how much time should I budget" tool of a line page (Release 4, Phase 4.5; METRICS.md "Trip-time
-- budget"). Pairs are every ordered pair of distinct stations a scheduled trip serves. A pair is
-- measured on a trip when both stops have a high/medium reconstructed arrival of the same run;
-- travel_s is the difference. The window is that of the arrival hour at the from station. Bounded:
-- the pair set is built per trip from scheduled stops, aggregated to the grain in the same pass
-- (never materialised per day), and only pairs with scheduled trips are kept.
with stops as (
    select
        s.trip_instance_key,
        s.service_date,
        s.line,
        s.direction_id,
        s.stop_sequence,
        st.station_id,
        st.station_name,
        s.scheduled_arrival_ts,
        s.hour_local
    from {{ ref('int_scheduled_stop_times') }} s
    join {{ ref('int_stop_stations') }} st on st.stop_id = s.stop_id
),

scheduled_pairs as (
    select
        a.trip_instance_key,
        a.service_date,
        a.line,
        a.direction_id,
        a.stop_sequence as from_seq,
        b.stop_sequence as to_seq,
        a.station_id as from_station_id,
        a.station_name as from_station_name,
        b.station_id as to_station_id,
        b.station_name as to_station_name,
        {{ time_band('a.scheduled_arrival_ts') }} as time_band,
        a.hour_local as from_hour,
        b.hour_local as to_hour
    from stops a
    join stops b
        on b.trip_instance_key = a.trip_instance_key
        and b.stop_sequence > a.stop_sequence
        and b.station_id <> a.station_id
),

measured as (
    select
        x.trip_instance_key,
        x.stop_sequence as from_seq,
        y.stop_sequence as to_seq,
        epoch(y.arrival_ts) - epoch(x.arrival_ts) as travel_s
    from {{ ref('int_usable_arrivals') }} x
    join {{ ref('int_usable_arrivals') }} y
        on y.trip_instance_key = x.trip_instance_key
        and y.segment = x.segment
        and y.stop_sequence > x.stop_sequence
    qualify row_number() over (
        partition by x.trip_instance_key, x.stop_sequence, y.stop_sequence
        order by abs(epoch(x.arrival_ts) - epoch(x.scheduled_arrival_ts)), x.segment
    ) = 1
),

trips as (
    select
        p.*,
        w.window_label,
        coalesce(f1.data_outage, false) or coalesce(f2.data_outage, false) as data_outage,
        d1.line is not null or d2.line is not null as service_disruption,
        m.travel_s
    from scheduled_pairs p
    join {{ ref('int_window_hours') }} w on w.service_date = p.service_date and w.hour_local = p.from_hour
    join {{ ref('int_hour_flags') }} f1 on f1.service_date = p.service_date and f1.hour_local = p.from_hour
    join {{ ref('int_hour_flags') }} f2 on f2.service_date = p.service_date and f2.hour_local = p.to_hour
    left join {{ ref('int_disrupted_line_hours') }} d1
        on d1.line = p.line and d1.service_date = p.service_date and d1.hour_local = p.from_hour
    left join {{ ref('int_disrupted_line_hours') }} d2
        on d2.line = p.line and d2.service_date = p.service_date and d2.hour_local = p.to_hour
    left join measured m
        on m.trip_instance_key = p.trip_instance_key and m.from_seq = p.from_seq and m.to_seq = p.to_seq
)

select
    line,
    direction_id,
    from_station_id,
    any_value(from_station_name) as from_station_name,
    to_station_id,
    any_value(to_station_name) as to_station_name,
    time_band,
    window_label,
    count(*) filter (where not data_outage) as n_scheduled_trips,
    count(*) filter (where data_outage) as n_excluded_outage,
    count(*) filter (where not data_outage and travel_s is not null and (travel_s <= 0 or travel_s > 10800)) as n_implausible,
    count(*) filter (where not data_outage and travel_s > 0 and travel_s <= 10800) as n_trips,
    count(*) filter (where not data_outage and travel_s > 0 and travel_s <= 10800 and service_disruption) as n_disruption,
    round(median(travel_s) filter (where not data_outage and travel_s > 0 and travel_s <= 10800), 1) as median_s,
    round(quantile_cont(travel_s, 0.95) filter (where not data_outage and travel_s > 0 and travel_s <= 10800), 1) as p95_s,
    round((quantile_cont(travel_s, 0.95) filter (where not data_outage and travel_s > 0 and travel_s <= 10800)
        - median(travel_s) filter (where not data_outage and travel_s > 0 and travel_s <= 10800))
        / nullif(median(travel_s) filter (where not data_outage and travel_s > 0 and travel_s <= 10800), 0), 4) as buffer_time_index,
    count(*) filter (where not data_outage and travel_s > 0 and travel_s <= 10800) as coverage_numerator,
    count(*) filter (where not data_outage) as coverage_denominator,
    100.0 * count(*) filter (where not data_outage and travel_s > 0 and travel_s <= 10800)
        / nullif(count(*) filter (where not data_outage), 0) as coverage_pct
from trips
group by line, direction_id, from_station_id, to_station_id, time_band, window_label
