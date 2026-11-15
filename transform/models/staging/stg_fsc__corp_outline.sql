-- One row per corporation in each stored answer of the corporate outline API.
-- The same corporation appears several times: once per disclosure snapshot,
-- and again under every name it was found by. The rules are in fsc_outline_rows
-- (src/company_signal/sql/functions.sql), which the lookup also uses.

select * from fsc_outline_rows('{{ source("collected_raw", "fsc_corp_outline_page") }}')
