-- Rolling months up into years must not lose or invent joiners and leavers.

with monthly as (
    select sum(new_subscriber_count) as joined, sum(lost_subscriber_count) as lost
    from {{ ref('mart_workplace_monthly') }}
),

yearly as (
    select sum(new_subscriber_total) as joined, sum(lost_subscriber_total) as lost
    from {{ ref('mart_workplace_yearly') }}
)

select *
from monthly, yearly
where monthly.joined <> yearly.joined or monthly.lost <> yearly.lost
