-- One row per workplace and base month.
--
-- The source has no workplace key, and only the first 6 digits of the business
-- registration number are published. The key is built from those 6 digits, the
-- normalized name and the date the workplace joined the pension scheme.
--
-- The address is deliberately not part of the key: about 2,400 workplaces a
-- month move, and with the legal-dong code in the key each of them looked like
-- one workplace closing and another opening. Source rows that share a key in
-- the same month (about 0.02% of rows) are units the data cannot tell apart;
-- they are summed and counted in source_row_count.
--
-- The rules are in nps_workplace_months (src/company_signal/sql/functions.sql), which the
-- lookup also uses.

select * from nps_workplace_months('{{ ref("stg_nps__workplace_record") }}', '{{ ref("int_nps__selected_publication") }}')
