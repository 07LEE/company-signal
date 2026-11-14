{# normalize_name: search and matching key for a workplace name.
   Drops legal-form markers and whitespace so that "(주)한빛", "주식회사 한빛"
   and "한빛" compare equal. The lookup CLI calls the same function. #}
{% macro create_functions() %}
create or replace macro normalize_name(name) as
    regexp_replace(
        upper(name),
        '\(주\)|㈜|주식회사|\(유\)|유한회사|\(합\)|합자회사|\(사\)|사단법인|\(재\)|재단법인|\s',
        '',
        'g'
    )
{% endmacro %}
