{% test not_null_where(model, column_name, where) %}
{#- Fails for rows matching `where` whose column is null. -#}
select *
from {{ model }}
where ({{ where }}) and {{ column_name }} is null
{% endtest %}
