-- Scheduled headways per line, direction, service date and hour of the later train: scheduled stop
-- times with a predecessor at their stop. The denominator of headway coverage.
select line, direction_id, service_date, hour_local, count(*) as scheduled_headways
from (
    select
        line, direction_id, service_date, hour_local,
        lag(scheduled_arrival_ts) over (
            partition by line, direction_id, service_date, stop_id
            order by scheduled_arrival_ts, trip_instance_key
        ) as previous_ts
    from {{ ref('int_scheduled_stop_times') }}
)
where previous_ts is not null
group by 1, 2, 3, 4
