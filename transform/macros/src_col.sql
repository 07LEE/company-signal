{# Read a canonical column from the raw JSON record, trying every known source name. #}
{% macro src_col(name) -%}
coalesce(
    {%- for source_name in var('nps_columns')[name] %}
    record ->> '{{ source_name }}'{{ "," if not loop.last }}
    {%- endfor %}
)
{%- endmacro %}
