-- Prediction time of the forecast served now (Release 3, ADR-019), per line with scheduled service
-- in the next hour. t is the latest quarter hour at or before the as-of time: the forecast_as_of
-- var (the transform sets it to the current time), or the latest successful capture when null
-- (local and e2e builds, so the sample builds the same rows every time). The grid has the columns
-- of int_forecast_grid, so the shared feature SQL (forecast_line_features) reads it unchanged.
-- Unlike the training grid it does not drop outage hours: it forecasts what is scheduled, and the
-- feature age says how fresh its inputs are.
with as_of as (
    select
        {% if var('forecast_as_of', none) is not none %}
        cast('{{ var("forecast_as_of") }}' as timestamp with time zone)
        {% else %}
        (select max(fetched_at) from {{ ref('int_captures') }} where status = 'ok')
        {% endif %} as as_of_ts
),

t as (
    select
        as_of_ts,
        time_bucket(interval 15 minute, as_of_ts) as prediction_ts
    from as_of
    where as_of_ts is not null
)

select distinct
    s.line,
    {{ service_date_from_ts('t.prediction_ts') }} as service_date,
    cast(extract(hour from t.prediction_ts at time zone '{{ var("timezone") }}') as integer) as hour_local,
    t.prediction_ts,
    case when dayofweek({{ service_date_from_ts('t.prediction_ts') }}) in (0, 6) then 'weekend' else 'weekday' end as day_type,
    t.as_of_ts
from t
join {{ ref('int_scheduled_stop_times') }} s
    on s.scheduled_arrival_ts >= t.prediction_ts + interval 15 minute
    and s.scheduled_arrival_ts < t.prediction_ts + interval 75 minute
