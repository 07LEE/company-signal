-- One row per workplace: its latest description, for search and selection,
-- plus the 12-month attrition, which is only given with 12 full months of history.

with latest as (

    select *
    from {{ ref('int_nps__workplace_monthly') }}
    qualify row_number() over (partition by workplace_id order by base_ym desc) = 1

),

history as (

    select
        workplace_id,
        min(base_ym) as first_base_ym,
        count(*)::integer as months_observed
    from {{ ref('mart_workplace_monthly') }}
    group by workplace_id

),

last_12_months as (

    select
        m.workplace_id,
        count(*) as month_count,
        sum(m.lost_subscriber_count) as lost_subscriber_count,
        avg(m.subscriber_count) as avg_subscriber_count
    from {{ ref('mart_workplace_monthly') }} as m
    inner join latest as l using (workplace_id)
    where m.base_ym > l.base_ym - interval 12 month
    group by m.workplace_id

),

data_bounds as (

    select max(base_ym) as latest_data_ym from {{ ref('int_nps__workplace_monthly') }}

)

select
    l.workplace_id,
    l.workplace_name,
    l.name_norm,
    l.biz_reg_no6,
    l.road_address,
    l.lot_address,
    l.legal_dong_code,
    l.industry_code,
    l.industry_name,
    l.workplace_type_code,
    l.join_status_code,
    l.applied_on,
    l.withdrawn_on,
    l.subscriber_count,
    l.base_ym as latest_base_ym,
    l.base_ym = b.latest_data_ym as in_latest_data,
    h.first_base_ym,
    h.months_observed,
    case
        when t.month_count = 12 and t.avg_subscriber_count > 0
            then t.lost_subscriber_count / t.avg_subscriber_count
    end as attrition_rate_12m
from latest as l
inner join history as h using (workplace_id)
left join last_12_months as t using (workplace_id)
cross join data_bounds as b
