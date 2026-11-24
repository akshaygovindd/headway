-- Daily weather written by the static Lambda (curated table=weather_daily, one part per date,
-- Release 3, ADR-022). The table is absent until the first weather fetch (and in replay and demo
-- builds), so the model checks for files first and returns no rows when there are none. A flag is
-- null when the value it needs is missing; such a day is left out of that comparison.
{% set pattern = var('curated_root') ~ '/curated/' ~ var('city_prefix') ~ '/table=weather_daily/*/*.parquet' %}
{% if execute %}
    {% set n_files = run_query("select count(*) from glob('" ~ pattern ~ "')").columns[0].values()[0] %}
{% else %}
    {% set n_files = 0 %}
{% endif %}

{% if n_files > 0 %}
select
    "date" as weather_date,
    snow_flag,
    heavy_rain_flag,
    extreme_heat_flag,
    snowfall_mm,
    precip_mm,
    tmax_c,
    source
from read_parquet('{{ pattern }}')
-- a refetched date is rewritten in place; keep one row per date regardless
qualify row_number() over (partition by "date" order by source) = 1
{% else %}
select
    cast(null as date) as weather_date,
    cast(null as boolean) as snow_flag,
    cast(null as boolean) as heavy_rain_flag,
    cast(null as boolean) as extreme_heat_flag,
    cast(null as double) as snowfall_mm,
    cast(null as double) as precip_mm,
    cast(null as double) as tmax_c,
    cast(null as varchar) as source
where false
{% endif %}
