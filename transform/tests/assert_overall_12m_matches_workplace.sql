-- The 12-month attrition is computed in two marts. They must agree.

select o.workplace_id
from {{ ref('mart_workplace_overall') }} as o
inner join {{ ref('mart_workplace') }} as w using (workplace_id)
where (o.attrition_rate_12m is null) <> (w.attrition_rate_12m is null)
   or abs(o.attrition_rate_12m - w.attrition_rate_12m) > 1e-9
