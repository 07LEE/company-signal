-- One row per corporation in each stored answer of the corporate outline API.
-- The same corporation appears several times: once per disclosure snapshot,
-- and again under every name it was found by.

select
    p.query_name,
    p.page,
    item.crno,
    item.bzno,
    item.corpNm as corp_name,
    try_strptime(nullif(item.enpEstbDt, ''), '%Y%m%d')::date as established_on,
    fsc_amount(item.enpEmpeCnt)::integer as employee_count,
    parse_tenure_years(item.empeAvgCnwkTermCtt) as avg_tenure_years,
    fsc_amount(item.enpPn1AvgSlryAmt) as avg_yearly_pay,
    nullif(trim(item.enpMainBizNm), '') as main_business,
    nullif(trim(item.sicNm), '') as industry_name,
    try_strptime(nullif(item.lastOpegDt, ''), '%Y%m%d')::date as published_on
from {{ source('collected_raw', 'fsc_corp_outline_page') }} as p,
    unnest(fsc_outline_items(p.body)) as t (item)
where item.crno is not null and item.crno <> ''
