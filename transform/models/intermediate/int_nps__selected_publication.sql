-- One publication per base month. The base month comes from the records, not
-- from the publication title. When a month was published more than once, the
-- latest publication wins; the others stay in raw only.
--
-- The rule is in nps_selected_publications (src/company_signal/sql/functions.sql),
-- which the lookup also uses.

select * from nps_selected_publications('{{ ref("stg_nps__workplace_record") }}')
