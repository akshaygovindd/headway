{% test not_null_where(model, column_name) %}
{#- Fails for rows whose column is null. dbt itself applies the test's `where` config as a filter
    on `model` (a subquery), so the macro must not take or re-apply a `where` argument: it arrives
    empty and would render `where ()`, which DuckDB 1.5.x rejects. -#}
select *
from {{ model }}
where {{ column_name }} is null
{% endtest %}
