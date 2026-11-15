-- Monthly indicators per workplace.
--
-- Rates use the previous month's subscriber count, and only when the previous
-- row really is the previous month. Estimated income divides the billed
-- contribution by headcount and the contribution rate; contributions are capped
-- by the income ceiling, so high-income workplaces are underestimated.
--
-- The rules are in nps_monthly (src/company_signal/sql/functions.sql), which the
-- lookup also uses.

select * from nps_monthly('{{ ref("int_nps__workplace_monthly") }}')
