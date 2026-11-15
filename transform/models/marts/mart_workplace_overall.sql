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

with monthly as (

    select * from {{ ref('mart_workplace_monthly') }}

),

totals as (

    select
        workplace_id,
        min(base_ym) as first_base_ym,
        max(base_ym) as last_base_ym,
        count(*)::integer as months_observed,
        arg_min(subscriber_count, base_ym) as first_subscriber_count,
        arg_max(subscriber_count, base_ym) as last_subscriber_count,
        avg(subscriber_count) as avg_subscriber_count,
        sum(new_subscriber_count)::integer as new_subscriber_total,
        sum(lost_subscriber_count)::integer as lost_subscriber_total,
        arg_min(est_avg_monthly_income, base_ym) filter (where est_avg_monthly_income is not null)
            as first_est_income,
        min(base_ym) filter (where est_avg_monthly_income is not null) as first_est_income_ym,
        arg_max(est_avg_monthly_income, base_ym) filter (where est_avg_monthly_income is not null)
            as last_est_income,
        max(base_ym) filter (where est_avg_monthly_income is not null) as last_est_income_ym
    from monthly
    group by workplace_id

),

-- Sums over the last 12 and the last 36 months of each workplace.
recent as (

    select
        m.workplace_id,
        count(*) filter (where m.base_ym > t.last_base_ym - interval 12 month) as months_12m,
        sum(m.new_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 12 month) as new_12m,
        sum(m.lost_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 12 month) as lost_12m,
        avg(m.subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 12 month) as avg_12m,
        count(*) filter (where m.base_ym > t.last_base_ym - interval 36 month) as months_3y,
        sum(m.new_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 36 month) as new_3y,
        sum(m.lost_subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 36 month) as lost_3y,
        avg(m.subscriber_count) filter (where m.base_ym > t.last_base_ym - interval 36 month) as avg_3y
    from monthly as m
    inner join totals as t using (workplace_id)
    group by m.workplace_id

),

rates as (

    select
        t.*,
        case
            when t.months_observed >= 12
                then t.lost_subscriber_total * 12.0 / t.months_observed / nullif(t.avg_subscriber_count, 0)
        end as attrition_rate_per_year,
        case
            when t.months_observed >= 12
                then least(t.new_subscriber_total, t.lost_subscriber_total) * 12.0
                    / t.months_observed / nullif(t.avg_subscriber_count, 0)
        end as replacement_rate_per_year,
        case when c.months_3y = 36 then c.lost_3y / 3.0 / nullif(c.avg_3y, 0) end as attrition_rate_3y,
        case when c.months_3y = 36 then least(c.new_3y, c.lost_3y) / 3.0 / nullif(c.avg_3y, 0) end
            as replacement_rate_3y,
        case when c.months_12m = 12 then c.lost_12m / nullif(c.avg_12m, 0) end as attrition_rate_12m,
        case when c.months_12m = 12 then least(c.new_12m, c.lost_12m) / nullif(c.avg_12m, 0) end
            as replacement_rate_12m,
        datediff('month', t.first_est_income_ym, t.last_est_income_ym) as income_span_months
    from totals as t
    inner join recent as c using (workplace_id)

)

select
    r.workplace_id,
    r.first_base_ym,
    r.last_base_ym,
    r.months_observed,
    r.first_subscriber_count,
    r.last_subscriber_count,
    r.avg_subscriber_count,
    r.new_subscriber_total,
    r.lost_subscriber_total,
    r.attrition_rate_per_year,
    r.attrition_rate_3y,
    r.attrition_rate_12m,
    r.replacement_rate_per_year,
    r.replacement_rate_3y,
    r.replacement_rate_12m,
    1 / nullif(r.attrition_rate_per_year, 0) as implied_tenure_years,
    1 / nullif(r.attrition_rate_3y, 0) as implied_tenure_years_3y,
    1 / nullif(r.attrition_rate_12m, 0) as implied_tenure_years_12m,
    r.first_est_income,
    r.last_est_income,
    -- Compound yearly growth between the first and the last month with an estimate.
    case
        when r.income_span_months >= 12 and r.first_est_income > 0
            then power(r.last_est_income / r.first_est_income, 12.0 / r.income_span_months) - 1
    end as income_growth_per_year,
    -- Nobody can have been there longer than the workplace has been in the scheme.
    datediff('month', w.applied_on, r.last_base_ym) / 12.0 as scheme_age_years
from rates as r
inner join {{ ref('mart_workplace') }} as w using (workplace_id)
