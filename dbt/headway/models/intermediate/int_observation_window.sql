-- Hours the pipeline was observing each feed. capture_minutes counts distinct
-- minutes with a successful, non-duplicate capture.
select
    service_date,
    hour_local,
    feed,
    count(distinct capture_minute) as capture_minutes,
    count(distinct capture_minute) >= 55 as window_complete
from {{ ref('int_captures') }}
where status = 'ok' and not is_duplicate_capture
group by service_date, hour_local, feed
