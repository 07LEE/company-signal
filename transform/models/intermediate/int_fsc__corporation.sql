-- One row per corporation, with the figures of its latest disclosure. The rules
-- are in fsc_corporations (macros/create_functions.sql), which the lookup also uses.

select * from fsc_corporations('{{ ref("stg_fsc__corp_outline") }}')
