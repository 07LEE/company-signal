-- Monthly indicators per workplace.
--
-- Rates use the previous month's subscriber count, and only when the previous
-- row really is the previous month. Estimated income divides the billed
-- contribution by headcount and the contribution rate; contributions are capped
-- by the income ceiling, so high-income workplaces are underestimated.

with monthly as (

    select
        workplace_id,
        base_ym,
        subscriber_count,
        billed_amount,
        new_subscriber_count,
        lost_subscriber_count,
        source_row_count,
        lag(base_ym) over by_workplace as previous_row_ym,
        lag(subscriber_count) over by_workplace as previous_row_subscriber_count
    from {{ ref('int_nps__workplace_monthly') }}
    window by_workplace as (partition by workplace_id order by base_ym)

),

with_previous as (

    select
        * exclude (previous_row_ym, previous_row_subscriber_count),
        case
            when previous_row_ym = base_ym - interval 1 month then previous_row_subscriber_count
        end as previous_subscriber_count
    from monthly

)

select
    m.workplace_id,
    m.base_ym,
    m.subscriber_count,
    m.billed_amount,
    m.new_subscriber_count,
    m.lost_subscriber_count,
    m.previous_subscriber_count,
    m.new_subscriber_count / nullif(m.previous_subscriber_count, 0) as hire_rate,
    m.lost_subscriber_count / nullif(m.previous_subscriber_count, 0) as attrition_rate,
    r.rate as contribution_rate,
    round(m.billed_amount / nullif(m.subscriber_count, 0) / r.rate)::bigint as est_avg_monthly_income,
    m.source_row_count
from with_previous as m
asof join {{ ref('nps_contribution_rate') }} as r
    on m.base_ym >= r.valid_from
