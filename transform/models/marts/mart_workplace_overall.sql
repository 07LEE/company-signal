-- One row per workplace: the whole collected period in a few numbers.
--
-- Attrition and replacement are given for three spans, each as a yearly rate:
-- the whole period, the last 3 years and the last 12 months, counted back from
-- the workplace's own latest month. A span is left empty unless every month of
-- it is present: 12 months for the last year, 36 for the last three, and at
-- least 12 for the whole period. Side by side they show whether the workplace
-- has always been like this or changed recently.
--
-- implied_tenure_years is 1 / yearly attrition: how long people would stay on
-- average if they kept leaving at that pace. It is not a measured tenure.
--
-- The rules are in nps_overall (src/company_signal/sql/functions.sql), which the
-- lookup also uses.

select * from nps_overall('{{ ref("mart_workplace_monthly") }}', '{{ ref("mart_workplace") }}')
