{#- The page files the collector saved, as a glob for nps_file_records. The data
    directory is the one profiles.yml uses for the databases. `pages` narrows
    the glob, for example to the first page of every publication. -#}
{% macro nps_page_files(pages='page-*') -%}
{{ env_var('COMPANY_SIGNAL_DATA_DIR', 'data') }}/raw/nps_workplace/*/{{ pages }}.json.gz
{%- endmacro %}
