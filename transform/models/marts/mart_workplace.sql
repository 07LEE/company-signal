-- One row per workplace: its latest description, for search and selection,
-- plus the 12-month attrition, which is only given with 12 full months of history.
--
-- The rules are in nps_workplaces (src/company_signal/sql/functions.sql), which the
-- lookup also uses.

select * from nps_workplaces('{{ ref("int_nps__workplace_monthly") }}', '{{ ref("mart_workplace_monthly") }}')
