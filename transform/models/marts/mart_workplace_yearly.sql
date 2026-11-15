-- One row per workplace and calendar year.
--
-- A year with fewer than 12 months of data is kept and flagged, not scaled up:
-- nine months of leavers are not a yearly attrition. Rates divide by the
-- average monthly headcount of the months that are present.
--
-- replacement_rate is the smaller of joiners and leavers over the headcount:
-- the share of staff that was swapped for someone else. A growing workplace has
-- many joiners and a low replacement rate; one that keeps losing and rehiring
-- people has a high one at the same headcount.
--
-- The rules are in nps_years (src/company_signal/sql/functions.sql), which the
-- lookup also uses.

select * from nps_years('{{ ref("mart_workplace_monthly") }}')
