-- A tied corporation's registration number starts with the workplace's 6 digits.

select l.workplace_id
from {{ ref('mart_workplace_company') }} as l
inner join {{ ref('mart_workplace') }} as w using (workplace_id)
where left(l.bzno, 6) <> w.biz_reg_no6
