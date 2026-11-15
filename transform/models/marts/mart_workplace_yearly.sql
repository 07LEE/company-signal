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

with yearly as (

    select
        workplace_id,
        year(base_ym)::integer as base_year,
        count(*)::integer as months_observed,
        avg(subscriber_count) as avg_subscriber_count,
        arg_min(subscriber_count, base_ym) as first_subscriber_count,
        arg_max(subscriber_count, base_ym) as last_subscriber_count,
        sum(new_subscriber_count)::integer as new_subscriber_total,
        sum(lost_subscriber_count)::integer as lost_subscriber_total,
        -- Total estimated income over total subscriber-months, so a month with
        -- more people weighs more.
        round(sum(billed_amount / contribution_rate) / nullif(sum(subscriber_count), 0))::bigint
            as est_avg_monthly_income
    from {{ ref('mart_workplace_monthly') }}
    group by workplace_id, year(base_ym)

)

select
    workplace_id,
    base_year,
    months_observed,
    months_observed = 12 as is_full_year,
    avg_subscriber_count,
    first_subscriber_count,
    last_subscriber_count,
    new_subscriber_total,
    lost_subscriber_total,
    lost_subscriber_total / nullif(avg_subscriber_count, 0) as attrition_rate,
    least(new_subscriber_total, lost_subscriber_total) / nullif(avg_subscriber_count, 0) as replacement_rate,
    est_avg_monthly_income,
    -- Change from the year before, only when the row before really is the year before.
    case
        when lag(base_year) over by_workplace = base_year - 1
            then est_avg_monthly_income / nullif(lag(est_avg_monthly_income) over by_workplace, 0) - 1
    end as income_change_rate
from yearly
window by_workplace as (partition by workplace_id order by base_year)
