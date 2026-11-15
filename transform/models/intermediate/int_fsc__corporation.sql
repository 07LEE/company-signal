-- One row per corporation, from its disclosure snapshots.
--
-- The figures (employees, tenure, pay) come together from the latest snapshot
-- that has any of them, so they describe the same report. Later snapshots often
-- carry the name only, with the figures left at 0. Descriptive fields take the
-- latest value that is filled in.

with item as (

    select * from {{ ref('stg_fsc__corp_outline') }}

),

figures as (

    select crno, employee_count, avg_tenure_years, avg_yearly_pay, published_on as figures_published_on
    from item
    where employee_count is not null or avg_tenure_years is not null or avg_yearly_pay is not null
    qualify row_number() over (partition by crno order by published_on desc nulls last, query_name, page) = 1

)

select
    i.crno,
    arg_max(i.corp_name, i.published_on) as corp_name,
    arg_max(i.bzno, i.published_on) as bzno,
    arg_max(i.established_on, i.published_on) filter (where i.established_on is not null) as established_on,
    arg_max(i.main_business, i.published_on) filter (where i.main_business is not null) as main_business,
    arg_max(i.industry_name, i.published_on) filter (where i.industry_name is not null) as industry_name,
    max(i.published_on) as latest_published_on,
    any_value(f.employee_count) as employee_count,
    any_value(f.avg_tenure_years) as avg_tenure_years,
    any_value(f.avg_yearly_pay) as avg_yearly_pay,
    any_value(f.figures_published_on) as figures_published_on
from item as i
left join figures as f using (crno)
group by i.crno
