{#- The completely collected publications and the date each was published, as a
    constant map from publication id to date, read from the collector's
    database when the model is compiled.

    The page records are filtered and dated with this map instead of a join.
    DuckDB cannot tell how many rows the page files hold and guesses none, so in
    a join with the publication table it holds all 68 million records in memory
    instead of the 132 publications. That made the staging build five times
    slower and spill to disk. A constant needs no join. -#}
{% macro nps_published_on() -%}
{%- if execute -%}
    {%- set rows = run_query(
        "select publication_id, title_date from " ~ source('collected_meta', 'publication')
        ~ " where status = 'complete' order by publication_id"
    ) -%}
    {%- set ids = [] -%}
    {%- set dates = [] -%}
    {%- for row in rows -%}
        {%- do ids.append("'" ~ row[0] | replace("'", "''") ~ "'") -%}
        {%- do dates.append("date '" ~ row[1] ~ "'") -%}
    {%- endfor -%}
    map([{{ ids | join(', ') }}]::varchar[], [{{ dates | join(', ') }}]::date[])
{%- else -%}
    map([]::varchar[], []::date[])
{%- endif -%}
{%- endmacro %}
