-- Observed slow segments against the MBTA's speed-restriction list (Release 4, Phase 4.5; METRICS.md
-- "Official slow-zone list"). One row per segment that is flagged (agg_slow_segments, any band, the
-- last_7d window: official restrictions last weeks, so the 24 h view is not compared) or listed (the hand-captured snapshot, ADR-025), plus one row per listed restriction with
-- no segment mapping (listed_unmapped, never dropped):
--   matched        flagged here and listed
--   observed_only  flagged here, not listed
--   listed_only    listed, not flagged here (observed_status says whether the segment had enough data)
--   listed_unmapped listed, not mapped to a segment (official_restriction_segments)
-- official_status is 'captured' when the snapshot has rows, else 'not_captured' (every flagged
-- segment is then observed_only and the site says "official list not captured yet"). The snapshot is
-- a snapshot: snapshot_date is its latest captured_on.
with flagged as (
    select
        line, direction_id, from_stop_id, to_stop_id,
        bool_or(flagged) as is_flagged,
        max(case severity when 'severe' then 3 when 'moderate' then 2 when 'minor' then 1 end) as severity_rank,
        max(excess_s) filter (where flagged) as worst_excess_s,
        max(n_trips) filter (where flagged) as n_trips,
        list(distinct time_band order by time_band) filter (where flagged) as flagged_bands,
        min(first_seen) filter (where flagged) as first_seen,
        max(last_seen) filter (where flagged) as last_seen,
        max(coverage_pct) as coverage_pct,
        max(n_trips) as max_n_trips
    from {{ ref('agg_slow_segments') }}
    where window_label = 'last_7d'
    group by 1, 2, 3, 4
),

official as (
    select * from {{ ref('stg_official_speed_restrictions') }}
),

meta as (
    select
        case when count(*) > 0 then 'captured' else 'not_captured' end as official_status,
        max(captured_on) as snapshot_date,
        max(source_url) as source_url
    from official
),

mapped as (
    select
        m.line,
        try_cast(m.direction_id as integer) as direction_id,
        m.from_stop_id,
        m.to_stop_id,
        list(distinct o.restriction_id order by o.restriction_id) as restriction_ids,
        min(o.speed_limit_mph) as official_speed_limit_mph,
        min(o.date_reported) as official_date_reported
    from {{ ref('official_restriction_segments') }} m
    join official o on o.restriction_id = m.restriction_id
    group by 1, 2, 3, 4
),

unmapped as (
    select o.*
    from official o
    where not exists (select 1 from {{ ref('official_restriction_segments') }} m where m.restriction_id = o.restriction_id)
),

segment_names as (
    select line, direction_id, from_stop_id, to_stop_id, any_value(from_stop_name) as from_stop_name,
        any_value(to_stop_name) as to_stop_name, min(segment_order) as segment_order
    from {{ ref('agg_headway_segment_window') }}
    group by 1, 2, 3, 4
),

segments as (
    select
        coalesce(f.line, m.line) as line,
        coalesce(f.direction_id, m.direction_id) as direction_id,
        coalesce(f.from_stop_id, m.from_stop_id) as from_stop_id,
        coalesce(f.to_stop_id, m.to_stop_id) as to_stop_id,
        coalesce(f.is_flagged, false) as is_flagged,
        m.restriction_ids is not null as is_listed,
        f.severity_rank, f.worst_excess_s, f.n_trips, f.flagged_bands, f.first_seen, f.last_seen,
        f.coverage_pct, f.max_n_trips,
        m.restriction_ids, m.official_speed_limit_mph, m.official_date_reported
    from flagged f
    full join mapped m
        on m.line = f.line and m.direction_id = f.direction_id
        and m.from_stop_id = f.from_stop_id and m.to_stop_id = f.to_stop_id
    where coalesce(f.is_flagged, false) or m.restriction_ids is not null
)

select
    s.line || ':' || s.direction_id || ':' || s.from_stop_id || ':' || s.to_stop_id as row_key,
    s.line,
    s.direction_id,
    s.from_stop_id,
    n.from_stop_name,
    s.to_stop_id,
    n.to_stop_name,
    n.segment_order,
    case
        when s.is_flagged and s.is_listed then 'matched'
        when s.is_flagged then 'observed_only'
        else 'listed_only'
    end as status,
    case
        when s.is_flagged then 'flagged'
        when coalesce(s.max_n_trips, 0) >= {{ var('slow_segments', {}).get('min_trips', 15) }} then 'not_flagged'
        else 'not_enough_data'
    end as observed_status,
    case s.severity_rank when 3 then 'severe' when 2 then 'moderate' when 1 then 'minor' end as severity,
    s.worst_excess_s,
    s.n_trips,
    coalesce(s.flagged_bands, []::varchar[]) as flagged_bands,
    s.first_seen,
    s.last_seen,
    s.coverage_pct,
    coalesce(s.restriction_ids, []::varchar[]) as restriction_ids,
    s.official_speed_limit_mph,
    s.official_date_reported,
    null::varchar as location_text,
    meta.official_status,
    meta.snapshot_date,
    meta.source_url,
    true as is_snapshot
from segments s
cross join meta
left join segment_names n
    on n.line = s.line and n.direction_id = s.direction_id
    and n.from_stop_id = s.from_stop_id and n.to_stop_id = s.to_stop_id

union all

select
    'unmapped:' || u.restriction_id,
    u.line,
    null::integer,
    null::varchar,
    null::varchar,
    null::varchar,
    null::varchar,
    null::integer,
    'listed_unmapped',
    'not_enough_data',
    null::varchar,
    null::double,
    null::bigint,
    []::varchar[],
    null::timestamptz,
    null::timestamptz,
    null::double,
    [u.restriction_id],
    u.speed_limit_mph,
    u.date_reported,
    u.location_text,
    meta.official_status,
    meta.snapshot_date,
    meta.source_url,
    true
from unmapped u
cross join meta
