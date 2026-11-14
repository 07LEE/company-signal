-- The grain of the monthly mart is one row per workplace and base month.

select workplace_id, base_ym
from {{ ref('mart_workplace_monthly') }}
group by workplace_id, base_ym
having count(*) > 1
