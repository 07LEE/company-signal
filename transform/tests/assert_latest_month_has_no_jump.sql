-- The latest month must not differ from the month before by more than 5% in
-- workplaces or subscribers. Over 2023-03 to 2026-09 only one month did
-- (2025-06, +9.6% workplaces), so a jump is worth a look: a publication cut
-- short, a column renamed so its values went missing, or a real change in what
-- the source covers. It warns rather than fails, because the last of these is
-- not an error. Only the latest month is checked, so a change that was looked
-- at once does not warn on every build.
{{ config(severity='warn') }}

{% set max_change = 0.05 %}

with profile as (

    select
        base_ym,
        workplace_count,
        subscriber_count,
        lag(base_ym) over by_month as previous_ym,
        lag(workplace_count) over by_month as previous_workplace_count,
        lag(subscriber_count) over by_month as previous_subscriber_count
    from {{ ref('mart_month_profile') }}
    window by_month as (order by base_ym)

)

select
    base_ym,
    previous_ym,
    workplace_count / previous_workplace_count - 1 as workplace_change,
    subscriber_count / previous_subscriber_count - 1 as subscriber_change
from profile
where base_ym = (select max(base_ym) from profile)
    and (
        abs(workplace_count / previous_workplace_count - 1) > {{ max_change }}
        or abs(subscriber_count / previous_subscriber_count - 1) > {{ max_change }}
    )
