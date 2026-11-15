-- One row per base month: the totals each new publication is checked against
-- (tests/assert_latest_month_has_no_jump.sql). The rules are in
-- nps_month_profile (src/company_signal/sql/functions.sql).

select * from nps_month_profile('{{ ref("int_nps__workplace_monthly") }}')
