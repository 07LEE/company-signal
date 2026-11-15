-- The grain of the yearly mart is one row per workplace and calendar year.

select workplace_id, base_year
from {{ ref('mart_workplace_yearly') }}
group by workplace_id, base_year
having count(*) > 1
