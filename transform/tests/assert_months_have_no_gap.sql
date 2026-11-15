-- Every month between the first and the latest must be there. A missing month
-- breaks the rates of the month after it, which need the previous month's
-- headcount. It warns rather than fails: the source itself has an empty
-- publication, and a month can be missing until it is collected again.
{{ config(severity='warn') }}

with bounds as (

    select min(base_ym) as first_ym, max(base_ym) as latest_ym
    from {{ ref('mart_month_profile') }}

)

select expected.base_ym
from bounds,
    unnest(generate_series(first_ym, latest_ym, interval 1 month)) as expected (base_ym)
where expected.base_ym not in (select base_ym from {{ ref('mart_month_profile') }})
