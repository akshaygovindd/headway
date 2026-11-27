{#
  Data invariants (docs/DATA_QUALITY.md): checks that the numbers in the marts are possible, not that
  the SQL ran. The tests are singular tests, one per release tier (a test tagged r2 is skipped
  when Release 2 is off, like the models it reads), and they find their columns by name, so a
  new mart column named like an existing one is covered without editing any yml.

  Each query returns the violating rows (model, column, invariant, value); no rows is a pass.
#}

{% macro invariant_relations(tier) %}
  {%- if tier == 'base' -%}
    {{ return([ref('agg_line_reliability_hourly'), ref('agg_line_reliability_window'), ref('agg_stop_deviation_daily')]) }}
  {%- elif tier == 'r2' -%}
    {{ return([
      ref('agg_arrival_reconstruction_daily'), ref('agg_prediction_accuracy_daily'),
      ref('agg_actual_deviation_hourly'), ref('agg_actual_deviation_daily'), ref('agg_actual_deviation_window'),
      ref('agg_headway_adherence_hourly'), ref('agg_headway_adherence_daily'), ref('agg_headway_window'),
      ref('agg_ett_daily'), ref('agg_ett_window'), ref('agg_accuracy_window'),
      ref('agg_countdown_accuracy_daily'), ref('agg_countdown_accuracy_window'),
      ref('agg_slo_daily'), ref('agg_slo_rollup')
    ]) }}
  {%- elif tier == 'r4' -%}
    {{ return([
      ref('agg_headway_segment_window'), ref('agg_ett_band_window'), ref('agg_trip_time_budget'),
      ref('agg_slow_segments'), ref('agg_slow_zone_comparison'), ref('agg_accessibility_outages')
    ]) }}
  {%- else -%}
    {{ exceptions.raise_compiler_error('unknown invariant tier ' ~ tier) }}
  {%- endif -%}
{% endmacro %}

{# A column's role from its name. Percentages are 0-100 (pct_*, share_*, *_coverage_pct), the one
   fraction, skipped_share, is 0-1 (METRICS.md units). #}
{% macro is_pct_column(c) -%}
  {{- c == 'coverage_pct' or c.startswith('pct_') or c.endswith('_coverage_pct') or c.startswith('share_')
      or c in ['target_pct', 'actual_pct'] -}}
{%- endmacro %}

{% macro is_fraction_column(c) -%}
  {{- c == 'skipped_share' -}}
{%- endmacro %}

{% macro is_count_column(c) -%}
  {{- c.startswith('n_') or c.endswith('_count')
      or c in ['coverage_numerator', 'coverage_denominator', 'scheduled_trip_stops', 'observable_trip_stops', 'trip_stops'] -}}
{%- endmacro %}

{% macro data_invariant_violations(tier) %}
  {%- set parts = [] -%}
  {%- set rels = invariant_relations(tier) -%}
  {%- if execute -%}
    {%- for rel in rels -%}
      {%- set cols = adapter.get_columns_in_relation(rel) -%}
      {%- set names = cols | map(attribute='name') | map('lower') | list -%}
      {%- for col in cols if col.is_number() -%}
        {%- set c = col.name | lower -%}
        {%- if is_pct_column(c) | trim == 'True' -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'share outside 0-100' as invariant, cast(" ~ c ~ " as varchar) as value from " ~ rel ~ " where " ~ c ~ " < 0 or " ~ c ~ " > 100") -%}
        {%- elif is_fraction_column(c) | trim == 'True' -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'fraction outside 0-1' as invariant, cast(" ~ c ~ " as varchar) as value from " ~ rel ~ " where " ~ c ~ " < 0 or " ~ c ~ " > 1") -%}
        {%- endif -%}
        {%- if is_count_column(c) | trim == 'True' -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'negative count' as invariant, cast(" ~ c ~ " as varchar) as value from " ~ rel ~ " where " ~ c ~ " < 0") -%}
        {%- endif -%}
        {%- if c.startswith('median_') -%}
          {%- set rest = c[7:] -%}
          {%- for q in ['p90_', 'p95_'] if (q ~ rest) in names -%}
            {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'median above " ~ q ~ rest ~ "' as invariant, " ~ c ~ " || ' > ' || " ~ q ~ rest ~ " as value from " ~ rel ~ " where " ~ c ~ " > " ~ q ~ rest) -%}
          {%- endfor -%}
        {%- endif -%}
        {%- if c.startswith('p90_') and ('p95_' ~ c[4:]) in names -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'p90 above p95' as invariant, " ~ c ~ " || ' > p95_" ~ c[4:] ~ "' as value from " ~ rel ~ " where " ~ c ~ " > p95_" ~ c[4:]) -%}
        {%- endif -%}
      {%- endfor -%}
      {%- if 'coverage_pct' in names and 'coverage_numerator' in names and 'coverage_denominator' in names -%}
        {#- the row's own numerator and denominator, never a line-wide value -#}
        {%- do parts.append("select '" ~ rel.identifier ~ "' as model, 'coverage_pct' as col, 'coverage_pct is not 100 * numerator / denominator of its own row' as invariant, coverage_pct || ' vs ' || coverage_numerator || '/' || coverage_denominator as value from " ~ rel ~ " where coverage_numerator > coverage_denominator or (coverage_denominator > 0 and abs(coverage_pct - 100.0 * coverage_numerator / coverage_denominator) > 0.06) or (coverage_denominator = 0 and coverage_pct is not null and coverage_pct <> 0)") -%}
      {%- endif -%}
    {%- endfor -%}
  {%- endif -%}
  {%- if parts | length == 0 -%}
    select cast(null as varchar) as model, cast(null as varchar) as col, cast(null as varchar) as invariant, cast(null as varchar) as value where false
  {%- else -%}
    {{ parts | join('\nunion all\n') }}
  {%- endif -%}
{% endmacro %}

{# Suspicious, not impossible: warn only. #}
{% macro data_suspicious_values(tier) %}
  {%- set parts = [] -%}
  {%- set rels = invariant_relations(tier) -%}
  {%- if execute -%}
    {%- for rel in rels -%}
      {%- set cols = adapter.get_columns_in_relation(rel) -%}
      {%- set names = cols | map(attribute='name') | map('lower') | list -%}
      {%- set n_col = names | select('in', ['n_arrivals', 'n_headways', 'n_eligible', 'n_trips']) | first -%}
      {%- for col in cols if col.is_number() -%}
        {%- set c = col.name | lower -%}
        {%- if c.startswith('median_') and 'deviation' in c and c.endswith('_s') -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'median deviation beyond 30 minutes' as invariant, cast(" ~ c ~ " as varchar) as value from " ~ rel ~ " where abs(" ~ c ~ ") > 1800") -%}
        {%- elif c == 'median_ratio' -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, 'median headway ratio outside 0.1-10' as invariant, cast(" ~ c ~ " as varchar) as value from " ~ rel ~ " where " ~ c ~ " < 0.1 or " ~ c ~ " > 10") -%}
        {%- elif c.startswith('pct_') and n_col -%}
          {%- do parts.append("select '" ~ rel.identifier ~ "' as model, '" ~ c ~ "' as col, '" ~ c ~ " exactly 0 or 100 over 200 or more observations' as invariant, cast(" ~ c ~ " as varchar) as value from " ~ rel ~ " where " ~ c ~ " in (0, 100) and " ~ n_col ~ " >= 200") -%}
        {%- endif -%}
      {%- endfor -%}
    {%- endfor -%}
  {%- endif -%}
  {%- if parts | length == 0 -%}
    select cast(null as varchar) as model, cast(null as varchar) as col, cast(null as varchar) as invariant, cast(null as varchar) as value where false
  {%- else -%}
    {{ parts | join('\nunion all\n') }}
  {%- endif -%}
{% endmacro %}
