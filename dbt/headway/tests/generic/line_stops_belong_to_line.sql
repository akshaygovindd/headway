{% test line_stops_belong_to_line(model, column_name, line_column='line') %}
{#- Fails for a stop (or station) that is not on the row's line in the static GTFS (int_line_stops). -#}
select m.{{ line_column }} as line, m.{{ column_name }} as stop
from {{ model }} m
where m.{{ column_name }} is not null
  and not exists (
    select 1
    from {{ ref('int_line_stops') }} l
    where l.line = m.{{ line_column }}
      and (l.stop_id = m.{{ column_name }} or l.station_id = m.{{ column_name }})
  )
{% endtest %}
