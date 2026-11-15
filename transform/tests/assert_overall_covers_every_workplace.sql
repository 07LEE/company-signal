-- Every workplace in the search table has exactly one overall row, and the
-- other way round.

select coalesce(w.workplace_id, o.workplace_id) as workplace_id
from {{ ref('mart_workplace') }} as w
full join {{ ref('mart_workplace_overall') }} as o using (workplace_id)
where w.workplace_id is null or o.workplace_id is null
